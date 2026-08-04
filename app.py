from __future__ import annotations

from dataclasses import dataclass
from io import BytesIO
import os
from typing import Any

import pandas as pd
import requests
from flask import Flask, render_template_string, request, send_file


BOM_ITEM_URL = "http://10.200.16.14/ords/wpo_mts/WCTX_ESTIMATE_API/BOM_ITEM"
DEFAULT_ORG_CODE = "WPN"
ORG_OPTIONS = {
    "WPN": "楠梓廠",
    "WPT": "樹谷廠",
    "WPD": "同奈廠",
}
REQUEST_TIMEOUT = 30
REPORT_COLUMNS = ["成品料號", "半成品料號", "原料料號", "原料數", "基重", "資源比率"]

app = Flask(__name__)


@dataclass
class AppResult:
    org_code: str
    item_no: str
    rows: list[dict[str, Any]]
    total_count: int
    error: str = ""


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
        body = response.json()
    except ValueError:
        return AppResult(
            org_code=org_code, item_no=stripped, rows=[], total_count=0,
            error="API 回傳非 JSON 格式，請聯絡系統管理員。",
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
    for item in data:
        item_3_list = item.get("item_3") or []
        item_3_str = (
            ", ".join(str(x) for x in item_3_list)
            if isinstance(item_3_list, list)
            else str(item_3_list)
        )
        count_val = item.get("item_3_count")
        rate_val = item.get("resource_rate")
        rows.append({
            "成品料號": str(item.get("item_9") or ""),
            "半成品料號": str(item.get("item_5") or ""),
            "原料料號": item_3_str,
            "原料數": "" if count_val is None else str(count_val),
            "基重": str(item.get("base_weight") or ""),
            "資源比率": "" if rate_val is None else str(rate_val),
        })

    return AppResult(
        org_code=org_code,
        item_no=stripped,
        rows=rows,
        total_count=len(rows),
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


def create_excel_file(report: AppResult) -> BytesIO:
    dataframe = pd.DataFrame(report.rows, columns=REPORT_COLUMNS)
    output = BytesIO()
    with pd.ExcelWriter(output, engine="openpyxl") as writer:
        dataframe.to_excel(writer, index=False, sheet_name="BOM報表")
    output.seek(0)
    return output


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
            excel_file = create_excel_file(report)
            name_part = f"_{report.item_no}" if report.item_no else ""
            filename = f"bom_report_{report.org_code}{name_part}.xlsx"
            return send_file(
                excel_file,
                as_attachment=True,
                download_name=filename,
                mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            )

    return render_template_string(
        PAGE_TEMPLATE,
        org_options=ORG_OPTIONS,
        selected_org_code=selected_org_code,
        item_no=item_no,
        report=report,
        columns=REPORT_COLUMNS,
    )


if __name__ == "__main__":
    app.run(
        debug=os.getenv("FLASK_DEBUG", "false").lower() == "true",
        host=os.getenv("FLASK_HOST", "0.0.0.0"),
        port=int(os.getenv("PORT", "5000")),
    )
