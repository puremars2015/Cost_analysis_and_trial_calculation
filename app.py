from __future__ import annotations

from dataclasses import dataclass
from io import BytesIO
import os
from typing import Any

import pandas as pd
import requests
from flask import Flask, Response, render_template_string, request, send_file


ITEM_MASTER_URL = "http://10.200.16.14/ords/wpo_mts/WIP_WORKORDER/ITEM_MASTER"
BOM_URL = "http://10.200.16.14/ords/wpo_mts/WIP_WORKORDER/BOM"
DEFAULT_ORG_CODE = "WPN"
ORG_OPTIONS = {
	"ALL": "全部廠別",
	"WPN": "楠梓廠",
	"WPT": "樹谷廠",
	"WPD": "同奈廠",
}
ALL_ORG_CODES = ["WPN", "WPT", "WPD"]
REQUEST_TIMEOUT = 300

app = Flask(__name__)


@dataclass
class AppResult:
	org_code: str
	rows: list[dict[str, str]]
	material_column_count: int
	total_finished_items: int
	errors: list[str]


class BomService:
	def __init__(self) -> None:
		self.session = requests.Session()
		self._bom_cache: dict[tuple[str, str], list[dict[str, Any]]] = {}

	def get_finished_items(self, org_code: str) -> list[dict[str, Any]]:
		payload = {"Org_code": org_code, "Item": "93"}
		data = self._put_json(ITEM_MASTER_URL, payload)
		items = data.get("wipItem", [])
		return [item for item in items if str(item.get("segment1", "")).startswith("93")]

	def get_bom(self, org_code: str, item_no: str) -> list[dict[str, Any]]:
		cache_key = (org_code, item_no)
		if cache_key in self._bom_cache:
			return self._bom_cache[cache_key]

		payload = {"Org_code": org_code, "Item_no": item_no}
		data = self._put_json(BOM_URL, payload)
		bom_items = data.get("wipBOMItem", [])
		self._bom_cache[cache_key] = bom_items
		return bom_items

	def resolve_raw_materials(self, org_code: str, item_no: str) -> list[tuple[str, str, str]]:
		raw_materials: list[tuple[str, str, str]] = []
		seen_materials: set[str] = set()
		self._collect_raw_materials(org_code, item_no, [], raw_materials, seen_materials)
		return raw_materials

	def _collect_raw_materials(
		self,
		org_code: str,
		item_no: str,
		stack: list[str],
		raw_materials: list[tuple[str, str, str]],
		seen_materials: set[str],
	) -> None:
		if item_no in stack:
			cycle = " -> ".join([*stack, item_no])
			raise ValueError(f"偵測到 BOM 循環: {cycle}")

		bom_items = self.get_bom(org_code, item_no)
		next_stack = [*stack, item_no]

		for component in bom_items:
			component_item = str(component.get("COMPONENT_ITEM") or "").strip()
			if not component_item:
				continue

			if component_item.startswith("53"):
				self._collect_raw_materials(org_code, component_item, next_stack, raw_materials, seen_materials)
				continue

			if component_item.startswith("3") and component_item not in seen_materials:
				desc = str(component.get("COMPONENT_DESC") or "").strip()
				qty = str(component.get("COMPONENT_QUANTITY") or "").strip()
				raw_materials.append((component_item, desc, qty))
				seen_materials.add(component_item)

	def _put_json(self, url: str, payload: dict[str, str]) -> dict[str, Any]:
		response = self.session.put(
			url,
			json=payload,
			headers={"accept": "application/json", "Content-Type": "application/json"},
			timeout=REQUEST_TIMEOUT,
		)
		response.raise_for_status()
		return response.json()


service = BomService()


def build_bom_report(org_code: str) -> AppResult:
	org_codes = ALL_ORG_CODES if org_code == "ALL" else [org_code]
	rows: list[dict[str, str]] = []
	errors: list[str] = []
	material_column_count = 0

	for current_org in org_codes:
		finished_items = service.get_finished_items(current_org)
		for item in finished_items:
			finished_item_no = str(item.get("segment1") or "").strip()
			finished_item_desc = str(item.get("description") or "").strip()
			if not finished_item_no:
				continue

			try:
				raw_materials = service.resolve_raw_materials(current_org, finished_item_no)
			except Exception as exc:
				errors.append(f"{finished_item_no} ({current_org}): {exc}")
				raw_materials = []

			material_column_count = max(material_column_count, len(raw_materials))
			row = {"成品料號": finished_item_no, "成品說明": finished_item_desc}
			for index, (raw_item, raw_desc, raw_qty) in enumerate(raw_materials, start=1):
				row[f"原料料號{index}"] = raw_item
				row[f"原料說明{index}"] = raw_desc
				row[f"用量{index}"] = raw_qty
			rows.append(row)

	normalized_rows = normalize_rows(rows, material_column_count)
	return AppResult(
		org_code=org_code,
		rows=normalized_rows,
		material_column_count=material_column_count,
		total_finished_items=len(normalized_rows),
		errors=errors,
	)


def normalize_rows(rows: list[dict[str, str]], material_column_count: int) -> list[dict[str, str]]:
	normalized_rows: list[dict[str, str]] = []
	for row in rows:
		normalized_row = {
			"成品料號": row.get("成品料號", ""),
			"成品說明": row.get("成品說明", ""),
		}
		for index in range(1, material_column_count + 1):
			item_col = f"原料料號{index}"
			desc_col = f"原料說明{index}"
			qty_col = f"用量{index}"
			normalized_row[item_col] = row.get(item_col, "")
			normalized_row[desc_col] = row.get(desc_col, "")
			normalized_row[qty_col] = row.get(qty_col, "")
		normalized_rows.append(normalized_row)
	return normalized_rows


def get_report_columns(material_column_count: int) -> list[str]:
	cols = ["成品料號", "成品說明"]
	for index in range(1, material_column_count + 1):
		cols.append(f"原料料號{index}")
		cols.append(f"原料說明{index}")
		cols.append(f"用量{index}")
	return cols


PAGE_TEMPLATE = """
<!doctype html>
<html lang="zh-Hant">
<head>
	<meta charset="utf-8">
	<meta name="viewport" content="width=device-width, initial-scale=1">
	<title>成本分析與試算系統 v0.1</title>
	<base href="/">
	<script>
		// 設置 base href 為當前路徑（例如 /marcom/ 或 /cost-analyze/）
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
		button,
		.button-link {
			min-height: 44px;
			border-radius: 12px;
			border: 1px solid var(--border);
			padding: 0 16px;
			font: inherit;
		}

		select {
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
			white-space: nowrap;
		}

		th {
			position: sticky;
			top: 0;
			background: #f7ead7;
		}

		.errors {
			background: var(--danger-bg);
			color: var(--danger-text);
		}

		.errors ul {
			margin: 12px 0 0;
			padding-left: 20px;
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
			<p>依照廠別抓取 93 開頭成品料號，遞迴展開 53 開頭半成品 BOM，直到取得 3 開頭原始原料，並產出成品與原料對照表。</p>

			<form method="get" action="" class="controls">
				<label>
					廠別
					<select name="org_code">
						{% for code, label in org_options.items() %}
						<option value="{{ code }}" {% if code == selected_org_code %}selected{% endif %}>{{ code }} - {{ label }}</option>
						{% endfor %}
					</select>
				</label>
				<button type="submit">載入 BOM 對照表</button>
				{% if report %}
				<a class="button-link" href="?org_code={{ report.org_code }}&export=1">下載 Excel</a>
				{% endif %}
			</form>
		</section>

		{% if report %}
		<section class="content">
			<div class="stats">
				<div class="stat">
					已處理成品數
					<strong>{{ report.total_finished_items }}</strong>
				</div>
				<div class="stat">
					原料欄位數
					<strong>{{ report.material_column_count }}</strong>
				</div>
				<div class="stat">
					目前廠別
					<strong>{{ org_options[report.org_code] }}</strong>
				</div>
			</div>

			{% if report.errors %}
			<div class="panel errors">
				<p><strong>以下成品料號處理失敗，已先略過：</strong></p>
				<ul>
					{% for error in report.errors %}
					<li>{{ error }}</li>
					{% endfor %}
				</ul>
			</div>
			{% endif %}

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
				<p class="muted">查無符合條件的成品與原料對照資料。</p>
				{% endif %}
			</div>
		</section>
		{% endif %}
	</main>
</body>
</html>
"""


def create_excel_file(report: AppResult) -> BytesIO:
	dataframe = pd.DataFrame(report.rows, columns=get_report_columns(report.material_column_count))
	output = BytesIO()
	with pd.ExcelWriter(output, engine="openpyxl") as writer:
		dataframe.to_excel(writer, index=False, sheet_name="BOM對照表")
	output.seek(0)
	return output


@app.get("/")
def index() -> str:
	selected_org_code = request.args.get("org_code", DEFAULT_ORG_CODE).upper()
	if selected_org_code not in ORG_OPTIONS:
		selected_org_code = DEFAULT_ORG_CODE

	report = None
	columns: list[str] = []

	# Handle export request (via query param for relative path)
	if request.args.get("export") == "1":
		report = build_bom_report(selected_org_code)
		excel_file = create_excel_file(report)
		filename = f"bom_report_{selected_org_code}.xlsx"
		return send_file(
			excel_file,
			as_attachment=True,
			download_name=filename,
			mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
		)

	if "org_code" in request.args:
		report = build_bom_report(selected_org_code)
		columns = get_report_columns(report.material_column_count)

	return render_template_string(
		PAGE_TEMPLATE,
		org_options=ORG_OPTIONS,
		selected_org_code=selected_org_code,
		report=report,
		columns=columns,
	)


if __name__ == "__main__":
	app.run(
		debug=os.getenv("FLASK_DEBUG", "false").lower() == "true",
		host=os.getenv("FLASK_HOST", "0.0.0.0"),
		port=int(os.getenv("PORT", "5000")),
	)
