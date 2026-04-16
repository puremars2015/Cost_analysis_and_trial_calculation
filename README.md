# 成本分析與試算系統

這是一個flask架構的網站，主要功能是從EBS跟Agile系統取得成品料號的BOM結構，並且將BOM結構中的原始料號進行成本分析與試算。

## v0.1 範圍

- 僅做 BOM 對照表預覽與 Excel 匯出
- `Item` 固定帶 `93`
- 遞迴展開 `53` 開頭半成品，直到取得 `3` 開頭原始原料
- 暫不處理成本試算
- 暫不處理替代料

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

## Docker

1. 建立 image

```bash
docker build -t cost-analysis-app .
```

2. 啟動 container

```bash
docker run --rm -p 5000:5000 cost-analysis-app
```

3. 開啟瀏覽器

```text
http://127.0.0.1:5000
```

## 透過共用 Nginx 代理

如果此專案要跟 `MarcomSys` 一起由同一個 Nginx container 對外提供，建議由 `MarcomSys/docker-compose.yml` 當總控：

- `http://<private-ip>:84` 進入 MarcomSys
- `http://<private-ip>:85` 進入本系統

此模式使用同 IP 不同 port 分流，不需要 DNS 或 hosts 設定。

## API 規格

1. 從api取得全部成品料號
curl -X 'PUT' \
  'http://10.200.16.14/ords/wpo_mts/WIP_WORKORDER/ITEM_MASTER' \
  -H 'accept: application/json' \
  -H 'Content-Type: application/json' \
  -d '{"Org_code":"WPN","Item":"93"}'

Item固定帶93，Org_code可以選WPN=楠梓廠、WPT=樹谷廠、WPD=同奈廠

回傳格式如下：

{
  "wipItem": [
    {
      "organization_id": 84,
      "inventory_item_id": 6691,
      "segment1": "93.BM080.400",
      "description": "NBR080-6 40cm*1500M",
      "ORGANIZATION_CODE": "WPN",
      "FROM_UOM_CODE": "kg",
      "TO_UOM_CODE": "rol",
      "CONVERSION_RATE": 48
    },
    {
      "organization_id": 84,
      "inventory_item_id": 44147,
      "segment1": "93.BM540.300",
      "description": "NBR540-6 30cm*3000M(A01)",
      "ORGANIZATION_CODE": "WPN",
      "FROM_UOM_CODE": "kg",
      "TO_UOM_CODE": "rol",
      "CONVERSION_RATE": 36
    },
    {
      "organization_id": 84,
      "inventory_item_id": 44148,
      "segment1": "93.BM540.960",
      "description": "NBR540-6 96cm*3500M(A01)",
      "ORGANIZATION_CODE": "WPN",
      "FROM_UOM_CODE": "kg",
      "TO_UOM_CODE": "rol",
      "CONVERSION_RATE": 134.4
    }
  ]
}



2. 從api取得成品料號的BOM結構

curl -X 'PUT' \
  'http://10.200.16.14/ords/wpo_mts/WIP_WORKORDER/BOM' \
  -H 'accept: application/json' \
  -H 'Content-Type: application/json' \
  -d '{"Org_code":"WPN","Item_no":"93.BM080.400"}'

回傳格式如下：

{
  "wipBOMItem": [
    {
      "ASSEMBLY_ITEM_ID": 6691,
      "ASSEMBLY_ITEM": "93.BM080.400",
      "ASSEMBLY_DESC": "NBR080-6 40cm*1500M",
      "COMPONENT_ITEM_ID": 5537,
      "COMPONENT_ITEM": "53.BM080.L00",
      "COMPONENT_DESC": "NBR080-6 410cm",
      "COMPONENT_QUANTITY": 1,
      "SUBSTITUTE_ITEM_ID": null,
      "SUBSTITUTE_ITEM": null,
      "SUBSTITUTE_DESC": null,
      "SUBSTITUTE_QUANTITY": null
    },
    {
      "ASSEMBLY_ITEM_ID": 6691,
      "ASSEMBLY_ITEM": "93.BM080.400",
      "ASSEMBLY_DESC": "NBR080-6 40cm*1500M",
      "COMPONENT_ITEM_ID": 3138,
      "COMPONENT_ITEM": "44.B08A0.001",
      "COMPONENT_DESC": "編織袋110cm*110cm",
      "COMPONENT_QUANTITY": 0.02083,
      "SUBSTITUTE_ITEM_ID": null,
      "SUBSTITUTE_ITEM": null,
      "SUBSTITUTE_DESC": null,
      "SUBSTITUTE_QUANTITY": null
    },
    {
      "ASSEMBLY_ITEM_ID": 6691,
      "ASSEMBLY_ITEM": "93.BM080.400",
      "ASSEMBLY_DESC": "NBR080-6 40cm*1500M",
      "COMPONENT_ITEM_ID": 2663,
      "COMPONENT_ITEM": "44.A039A.400",
      "COMPONENT_DESC": "3\"(77mm)*9mm*400mm一般紙管",
      "COMPONENT_QUANTITY": 0.02083,
      "SUBSTITUTE_ITEM_ID": null,
      "SUBSTITUTE_ITEM": null,
      "SUBSTITUTE_DESC": null,
      "SUBSTITUTE_QUANTITY": null
    }
  ]
}

然後這邊,會需要挑出其中的COMPONENT_ITEM,去呼叫第三個api取得原始料的料號,只需要挑53開頭的去再查一次BOM的API,因為53開頭的是半成品,93開頭是成品,3開頭的才是原始原料

3. 從api取得原始料號的料號

curl -X 'PUT' \
  'http://10.200.16.14/ords/wpo_mts/WIP_WORKORDER/BOM' \
  -H 'accept: application/json' \
  -H 'Content-Type: application/json' \
  -d '{"Org_code":"WPN","Item_no":"53.BM080.L00"}'

回傳格式如同前一步驟,因為是同一個API,只是傳入的Item_no不同,回傳的BOM結構也會不同,這邊就不再重複貼一次了

4. 製作成品跟原料的BOM對照表,結構是

成品料號 | 原料料號1 | 原料料號2 | 原料料號3 | ...
---|---|---|---|---


5. 做成網頁的table,給使用者預覽,並另外提供下載成Excel的功能