# 成本分析與試算系統

這是一個 Flask 架構的網站，依廠別查詢 BOM 成本報表，並提供 Excel 匯出功能。

## 執行方式

1. 安裝套件

```bash
pip install -r requirements.txt
```

2. 啟動網站

```bash
python app.py
```

3. 開啟瀏覽器

```text
http://127.0.0.1:5000
```

## 資料來源

唯一資料來源為以下 REST API（GET）：

```
GET http://10.200.16.14/ords/wpo_mts/WCTX_ESTIMATE_API/BOM_ITEM
```

### 查詢參數

| 參數       | 必填 | 說明                                     |
|-----------|------|------------------------------------------|
| `org_code` | 必填 | 廠別代碼，可選值：`WPN`（楠梓廠）、`WPT`（樹谷廠）、`WPD`（同奈廠） |
| `item_no`  | 選填 | 成品料號篩選。**留白時不送出此參數**，API 會回傳該廠所有料號 |

範例（查詢全部）：

```
GET .../WCTX_ESTIMATE_API/BOM_ITEM?org_code=WPN
```

範例（指定料號）：

```
GET .../WCTX_ESTIMATE_API/BOM_ITEM?org_code=WPN&item_no=93.00058.200
```

### API 回傳格式

```json
{
  "status": "S",
  "data": [
    {
      "item_9": "93.00058.200",
      "item_5": "53.00058.L00",
      "item_3": ["3.XXXXX.XXX"],
      "item_3_count": 2,
      "base_weight": 1.5,
      "resource_rate": 0.85
    }
  ]
}
```

`status` 為 `"S"` 時代表成功；否則 `message` 欄位含錯誤說明。

### 回傳表格欄位

| 欄位     | 來源欄位          | 說明                         |
|---------|-------------------|------------------------------|
| 成品料號 | `item_9`          | 93 開頭成品料號               |
| 半成品料號 | `item_5`        | 53 開頭半成品料號             |
| 原料料號 | `item_3`（陣列）  | 3 開頭原料料號，多筆以逗號分隔 |
| 原料數   | `item_3_count`    | 原料種類數                    |
| 基重     | `base_weight`     | 基重                          |
| 資源比率 | `resource_rate`   | 資源佔比                      |

## 已停用功能（舊版）

以下 API 與邏輯在舊版（v0.1 初版）中使用，現已**全部停止使用**：

- `PUT WIP_WORKORDER/ITEM_MASTER` — 取得成品料號清單
- `PUT WIP_WORKORDER/BOM` — 取得 BOM 結構
- 遞迴展開 53 開頭半成品的邏輯（逐層呼叫 BOM API 直到取得 3 開頭原料）

現在由 `WCTX_ESTIMATE_API/BOM_ITEM` 單一 GET 端點一次回傳展開後的完整 BOM 對照資料，不再需要客戶端遞迴。
