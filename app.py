from __future__ import annotations

from dataclasses import dataclass
from io import BytesIO
import json
import os
import re
from typing import Any

import pandas as pd
import requests
from flask import Flask, abort, g, redirect, render_template_string, request, session, url_for
from werkzeug.middleware.proxy_fix import ProxyFix

import db
from auth import auth_bp
from admin_users import admin_bp


BOM_ITEM_URL = "http://10.200.16.14/ords/wpo_mts/WCTX_ESTIMATE_API/BOM_ITEM"
DEFAULT_ORG_CODE = "WPN"
ORG_OPTIONS = {
    "WPN": "楠梓廠",
    "WPT": "樹谷廠",
    "WPD": "同奈廠",
}
REQUEST_TIMEOUT = 30

# Paths that never require authentication
_AUTH_WHITELIST = {"/auth/login", "/auth/logout", "/health"}

app = Flask(__name__)
app.wsgi_app = ProxyFix(app.wsgi_app, x_prefix=1)
_session_secret = os.environ.get("BOM_SESSION_SECRET")
if not _session_secret:
    raise RuntimeError("BOM_SESSION_SECRET must be configured")
app.secret_key = _session_secret
app.config["SESSION_COOKIE_HTTPONLY"] = True
app.config["SESSION_COOKIE_SAMESITE"] = "Lax"
# SESSION_COOKIE_SECURE 根据请求协议动态设置（在 before_request 中）

app.register_blueprint(auth_bp)
app.register_blueprint(admin_bp)
app.teardown_appcontext(db.close_db)

# Matches bare leading-decimal numbers at JSON value positions, e.g. :.91 or [.91
# Only fires when the decimal is followed by , ] or } so string content is unlikely to match.
_BARE_DECIMAL_RE = re.compile(r'(?P<prefix>[:\[,]\s*)\.(?P<fraction>\d+)(?=[,\]}])')


@app.before_request
def _auth_gate():
    # 動態設置 SESSION_COOKIE_SECURE：根據 X-Forwarded-Proto 判斷是否 HTTPS
    # 这样 HTTP 和 HTTPS 都可以保持 session
    proto = request.headers.get("X-Forwarded-Proto", "http")
    app.config["SESSION_COOKIE_SECURE"] = (proto == "https")

    path = request.path
    # Allow static files and whitelisted paths
    if path.startswith("/static"):
        return
    for prefix in _AUTH_WHITELIST:
        if path == prefix or path.startswith(prefix + "/"):
            return

    guid = session.get("user_guid")
    if not guid:
        if request.is_json:
            from flask import jsonify
            return jsonify({"error": "Unauthorized"}), 401
        next_url = f"{request.script_root}{request.full_path.rstrip('?')}"
        return redirect(url_for("auth.login", next=next_url))

    # Re-check active status on every request. The injectable loader keeps
    # authentication tests isolated from the SQL Server container.
    user_loader = app.config.get("AUTH_USER_LOADER", db.get_user_by_guid)
    user = user_loader(guid)
    if not user or not user["IS_ACTIVE"]:
        session.clear()
        if request.is_json:
            from flask import jsonify
            return jsonify({"error": "Unauthorized"}), 401
        return redirect(url_for("auth.login"))

    # A first-login/reset user may only change password or log out.
    if user.get("MUST_CHANGE_PASSWORD") and path != "/auth/change-password":
        return redirect(url_for("auth.change_password"))


def _parse_response_json(response: Any) -> Any:
    try:
        return response.json()
    except ValueError:
        pass
    # Upstream API sometimes emits bare decimals like :.91 — normalize then retry parse.
    try:
        normalized = _BARE_DECIMAL_RE.sub(r'\g<prefix>0.\g<fraction>', response.text)
        return json.loads(normalized)
    except (ValueError, TypeError):
        raise ValueError("cannot parse response as JSON")


@dataclass
class AppResult:
    org_code: str
    item_no: str
    rows: list[dict[str, Any]]
    total_count: int
    error: str = ""
    max_item_3_count: int = 0  # 最大原料數量，用於動態生成原料欄位


def fetch_bom_items(org_code: str, item_no: str) -> AppResult:
    stripped = item_no.strip()
    params: dict[str, str] = {"org_code": org_code}
    if stripped:
        params["item_no"] = stripped

    try:
        response = requests.get(BOM_ITEM_URL, params=params, timeout=REQUEST_TIMEOUT)
        response.raise_for_status()
    except requests.exceptions.Timeout:
        return AppResult(
            org_code=org_code, item_no=stripped, rows=[], total_count=0,
            error="API 請求逾時，請稍後再試。",
        )
    except requests.exceptions.RequestException:
        return AppResult(
            org_code=org_code, item_no=stripped, rows=[], total_count=0,
            error="API 請求失敗，請確認網路連線或聯絡系統管理員。",
        )

    try:
        body = _parse_response_json(response)
    except ValueError:
        try:
            response = requests.get(BOM_ITEM_URL, params=params, timeout=REQUEST_TIMEOUT)
            response.raise_for_status()
            body = _parse_response_json(response)
        except ValueError:
            return AppResult(
                org_code=org_code, item_no=stripped, rows=[], total_count=0,
                error="上游 API 暫時回應異常，請稍後再試。",
            )
        except requests.exceptions.RequestException:
            return AppResult(
                org_code=org_code, item_no=stripped, rows=[], total_count=0,
                error="API 請求失敗，請確認網路連線或聯絡系統管理員。",
            )

    if not isinstance(body, dict):
        return AppResult(
            org_code=org_code, item_no=stripped, rows=[], total_count=0,
            error="API 回傳格式異常，請聯絡系統管理員。",
        )

    if body.get("status") != "S":
        msg = body.get("message") or "未知錯誤"
        return AppResult(
            org_code=org_code, item_no=stripped, rows=[], total_count=0,
            error=f"API 回傳錯誤：{msg}",
        )

    data = body.get("data")
    if not isinstance(data, list):
        return AppResult(
            org_code=org_code, item_no=stripped, rows=[], total_count=0,
            error="API 回傳 data 欄位格式異常，請聯絡系統管理員。",
        )

    rows: list[dict[str, Any]] = []
    max_item_3_count = 0

    for item in data:
        item_3_list = item.get("item_3") or []
        if not isinstance(item_3_list, list):
            item_3_list = [item_3_list] if item_3_list else []

        # 更新最大原料數
        current_count = len(item_3_list)
        if current_count > max_item_3_count:
            max_item_3_count = current_count

        # 展開原料為多個欄位
        item_3_fields = {f"原料{i+1}": str(x) for i, x in enumerate(item_3_list)}

        count_val = item.get("item_3_count")
        rate_val = item.get("resource_rate")

        row = {
            "成品料號": str(item.get("item_9") or ""),
            "半成品料號": str(item.get("item_5") or ""),
            "原料數": "" if count_val is None else str(count_val),
            "基重": str(item.get("base_weight") or ""),
            "resource rate": "" if rate_val is None else str(rate_val),
        }
        # 加入展開後的原料欄位
        row.update(item_3_fields)
        rows.append(row)

    return AppResult(
        org_code=org_code,
        item_no=stripped,
        rows=rows,
        total_count=len(rows),
        max_item_3_count=max_item_3_count,
    )


PAGE_TEMPLATE = """
<!doctype html>
<html lang="zh-Hant">
<head>
	<meta charset="utf-8">
	<meta name="viewport" content="width=device-width, initial-scale=1">
	<title>成本分析與試算系統 v0.1</title>
	<base href="/">
	<script>
		(function() {
			var path = window.location.pathname;
			if (path.match(/^\/(marcom|cost-analyze)\//)) {
				var baseTag = document.querySelector('base');
				if (baseTag) {
					baseTag.href = path.replace(/\/[^\/]*$/, '/');
				}
			}
		})();
	</script>
	<style>
		:root {
			color-scheme: light;
			--bg: #f4efe7;
			--panel: #fffaf2;
			--panel-strong: #f2e3c8;
			--text: #2d241b;
			--muted: #6f6255;
			--accent: #8f4e28;
			--accent-hover: #713d1d;
			--border: #d6c4ae;
			--danger-bg: #fce8e6;
			--danger-text: #8a1c12;
		}

		* {
			box-sizing: border-box;
		}

		body {
			margin: 0;
			font-family: "Noto Sans TC", "Microsoft JhengHei", sans-serif;
			color: var(--text);
			background:
				radial-gradient(circle at top right, rgba(143, 78, 40, 0.12), transparent 28%),
				linear-gradient(180deg, #f7f1e7 0%, var(--bg) 100%);
		}

		main {
			max-width: 1280px;
			margin: 0 auto;
			padding: 32px 20px 48px;
		}

		.hero {
			background: linear-gradient(135deg, rgba(255, 250, 242, 0.96), rgba(242, 227, 200, 0.88));
			border: 1px solid rgba(143, 78, 40, 0.15);
			border-radius: 24px;
			padding: 28px;
			box-shadow: 0 16px 40px rgba(72, 48, 28, 0.08);
		}

		h1 {
			margin: 0 0 8px;
			font-size: clamp(2rem, 3vw, 3rem);
		}

		p {
			margin: 0;
			line-height: 1.6;
		}

		.user-bar {
			display: flex;
			justify-content: flex-end;
			align-items: center;
			gap: 12px;
			margin-bottom: 12px;
			font-size: 0.9rem;
			color: var(--muted);
		}

		.user-bar a {
			color: var(--accent);
			text-decoration: none;
		}

		.controls {
			display: flex;
			flex-wrap: wrap;
			gap: 12px;
			margin-top: 24px;
			align-items: end;
		}

		label {
			display: flex;
			flex-direction: column;
			gap: 8px;
			font-weight: 600;
		}

		select,
		input[type="text"],
		button,
		.button-link {
			min-height: 44px;
			border-radius: 12px;
			border: 1px solid var(--border);
			padding: 0 16px;
			font: inherit;
		}

		select,
		input[type="text"] {
			min-width: 220px;
			background: #fff;
			color: var(--text);
		}

		button,
		.button-link {
			background: var(--accent);
			color: #fff;
			cursor: pointer;
			transition: background 0.2s ease, transform 0.2s ease;
			text-decoration: none;
			display: inline-flex;
			align-items: center;
			justify-content: center;
		}

		button:hover,
		.button-link:hover {
			background: var(--accent-hover);
			transform: translateY(-1px);
		}

		.content {
			margin-top: 24px;
			display: grid;
			gap: 20px;
		}

		.panel {
			background: rgba(255, 250, 242, 0.94);
			border: 1px solid var(--border);
			border-radius: 20px;
			padding: 20px;
			box-shadow: 0 12px 30px rgba(72, 48, 28, 0.06);
		}

		.stats {
			display: flex;
			flex-wrap: wrap;
			gap: 16px;
		}

		.stat {
			flex: 1 1 220px;
			background: var(--panel-strong);
			border-radius: 16px;
			padding: 16px;
		}

		.stat strong {
			display: block;
			font-size: 1.75rem;
			margin-top: 6px;
		}

		.table-wrap {
			overflow-x: auto;
		}

		table {
			width: 100%;
			border-collapse: collapse;
			min-width: 680px;
		}

		th,
		td {
			padding: 12px 14px;
			border-bottom: 1px solid rgba(214, 196, 174, 0.7);
			text-align: left;
			vertical-align: top;
		}

		th {
			position: sticky;
			top: 0;
			background: #f7ead7;
			white-space: nowrap;
		}

		.errors {
			background: var(--danger-bg);
			color: var(--danger-text);
		}

		.muted {
			color: var(--muted);
		}

		@media (max-width: 720px) {
			main {
				padding: 20px 14px 32px;
			}

			.hero,
			.panel {
				padding: 18px;
			}

			.controls {
				flex-direction: column;
				align-items: stretch;
			}

			select,
			input[type="text"],
			button,
			.button-link {
				width: 100%;
			}
		}
	</style>
</head>
<body>
	<main>
		<div class="user-bar">
			{% if current_user %}
			{{ current_user.name }}（{{ current_user.role }}）
			{% if current_user.role == 'ADMIN' %}
			· <a href="{{ url_for('admin.users') }}">會員管理</a>
			{% endif %}
			· <a href="{{ url_for('auth.change_password') }}">修改密碼</a>
			· <a href="{{ url_for('auth.logout') }}">登出</a>
			{% endif %}
		</div>
		<section class="hero">
			<h1>成本分析與試算系統 v0.1</h1>
			<p>依廠別查詢 BOM 成本報表。成品料號可留白以查詢全部，或輸入特定料號篩選。</p>

			<form method="get" action="" class="controls">
				<label>
					廠別
					<select name="org_code">
						{% for code, label in org_options.items() %}
						<option value="{{ code }}" {% if code == selected_org_code %}selected{% endif %}>{{ code }} - {{ label }}</option>
						{% endfor %}
					</select>
				</label>
				<label>
					成品料號（可留白）
					<input type="text" name="item_no" value="{{ item_no }}" placeholder="例：93.00058.200">
				</label>
				<button type="submit">查詢 BOM 報表</button>
				{% if report and not report.error %}
				<a class="button-link" href="?org_code={{ report.org_code }}{% if report.item_no %}&amp;item_no={{ report.item_no|urlencode }}{% endif %}&amp;export=1">下載 Excel</a>
				{% endif %}
			</form>
		</section>

		{% if report %}
		<section class="content">
			{% if report.error %}
			<div class="panel errors">
				<p><strong>查詢錯誤：</strong>{{ report.error }}</p>
			</div>
			{% else %}
			<div class="stats">
				<div class="stat">
					查詢筆數
					<strong>{{ report.total_count }}</strong>
				</div>
				<div class="stat">
					廠別
					<strong>{{ org_options[report.org_code] }}</strong>
				</div>
				<div class="stat">
					成品料號篩選
					<strong>{{ report.item_no if report.item_no else "（全部）" }}</strong>
				</div>
			</div>

			<div class="panel table-wrap">
				{% if report.rows %}
				<table>
					<thead>
						<tr>
							{% for column in columns %}
							<th>{{ column }}</th>
							{% endfor %}
						</tr>
					</thead>
					<tbody>
						{% for row in report.rows %}
						<tr>
							{% for column in columns %}
							<td>{{ row[column] }}</td>
							{% endfor %}
						</tr>
						{% endfor %}
					</tbody>
				</table>
				{% else %}
				<p class="muted">查無符合條件的 BOM 資料。</p>
				{% endif %}
			</div>
			{% endif %}
		</section>
		{% endif %}
	</main>
</body>
</html>
"""


def create_excel_file(report: AppResult, columns: list[str]) -> BytesIO:
    dataframe = pd.DataFrame(report.rows, columns=columns)
    output = BytesIO()
    with pd.ExcelWriter(output, engine="openpyxl") as writer:
        dataframe.to_excel(writer, index=False, sheet_name="BOM報表")
    output.seek(0)
    return output


def _current_user():
    guid = session.get("user_guid")
    if not guid:
        return None
    from types import SimpleNamespace
    return SimpleNamespace(
        guid=guid,
        account=session.get("account", ""),
        name=session.get("name", ""),
        role=session.get("role", ""),
    )


def build_report_columns(max_item_3_count: int) -> list[str]:
    """根據最大原料數動態生成報表欄位"""
    base_columns = ["成品料號", "半成品料號", "原料數", "基重", "resource rate"]
    item_3_columns = [f"原料{i+1}" for i in range(max_item_3_count)]
    return base_columns + item_3_columns


@app.get("/health")
def health():
    from flask import jsonify
    return jsonify({"ok": True})


@app.get("/")
def index():
    selected_org_code = request.args.get("org_code", DEFAULT_ORG_CODE).upper()
    if selected_org_code not in ORG_OPTIONS:
        selected_org_code = DEFAULT_ORG_CODE

    item_no = request.args.get("item_no", "")
    report = None

    if "org_code" in request.args:
        report = fetch_bom_items(selected_org_code, item_no)

        if request.args.get("export") == "1" and not report.error:
            excel_columns = build_report_columns(report.max_item_3_count)
            excel_file = create_excel_file(report, excel_columns)
            name_part = f"_{report.item_no}" if report.item_no else ""
            filename = f"bom_report_{report.org_code}{name_part}.xlsx"
            from flask import send_file
            return send_file(
                excel_file,
                as_attachment=True,
                download_name=filename,
                mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            )

    # 頁面渲染時的欄位
    page_columns = build_report_columns(report.max_item_3_count) if report and report.rows else []

    return render_template_string(
        PAGE_TEMPLATE,
        org_options=ORG_OPTIONS,
        selected_org_code=selected_org_code,
        item_no=item_no,
        report=report,
        columns=page_columns,
        current_user=_current_user(),
    )


if __name__ == "__main__":
    app.run(
        debug=os.getenv("FLASK_DEBUG", "false").lower() == "true",
        host=os.getenv("FLASK_HOST", "0.0.0.0"),
        port=int(os.getenv("PORT", "5000")),
    )
