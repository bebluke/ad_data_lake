# AD Data Lake MVP 技術規格與架構說明

## 1. 專案定位
- **使命**：整合 Meta Marketing API 的 Campaign/Ad/Creative 資料流，打造一條自動化資料湖（RAW JSON）與行銷工具（Streamlit）的共用平台。
- **範疇**：
  - Data Lake：排程式擷取、備援查詢、Insights 回補，輸出結構化 JSON。
  - 行銷營運工具：Campaign/Ad Set/Ad/Creative 的查詢、調整、複製與建立。
  - 規則/淨化層（Sanitize Layer）：將 GET 到的資料轉換為適合 POST 的參數（型別、欄位、預設值），串起 GET → EDIT → POST 的閉環。
- **主要依賴**：Python 3.11、facebook_business SDK、Streamlit、dotenv、PyYAML。

## 2. 高階架構總覽
```
┌──────────────────────────────┐
│ configs/ & .env              │  ← 設定、密鑰
└──────────────┬───────────────┘
               │ load
        ┌──────▼─────────────────────┐
        │ Data Lake (src/main_*)     │  ← 批次擷取 & JSON 輸出
        └──────┬─────────────────────┘
               │ write RAW JSON
        ┌──────▼─────────────────────┐
        │ output/<date>/             │  ← RAW 資料層
        └──────┬─────────────────────┘
               │ read / inject
┌──────────────▼─────────────────────┐
│ GET 工具層                        │  ← Inspector、Pixels Exporter
├──────────────┬─────────────────────┤
│ EDIT 層 (Streamlit pages/)         │  ← 表單編輯、UI 體驗
├──────────────┬─────────────────────┤
│ POST 層 (Meta API)                 │  ← create_* 呼叫
└──────────────▼─────────────────────┘
        │ sanitize_payload + 自訂規則
        ▼
  Sanitize / Rules Layer (src/utils/api_helpers.py, pages/1_Campaign_Cloner.py)
```

## 3. 模組分層明細
| 分類 | 主要路徑 | 職責摘要 |
| ---- | -------- | -------- |
| 設定與 Schema | `configs/`, `src/configs/fields_schema.py` | 存放 ad account、mode、日期區間與各物件欄位定義（含 GET/POST fields，中文標籤）。 |
| Data Lake 擷取 | `src/main_extractor.py` | 依設定批次擷取 Campaign/Ad Set/Ad/Creative/Insights，內建批次請求、重試、JSON 儲存。 |
| API 抽象層 | `src/extractors/api_extractor.py`, `src/extractors/get_pixels.py` | 封裝非同步 Insights polling、Creative 端點滾動擷取、Pixel 匯出。 |
| 行銷工具 (GET) | `src/tools/campaign_inspector.py` | 以 Campaign 為中心查詢 Campaign → Ad Set → Ad → Creative 並附加 Pixel，輸出報告。 |
| 行銷工具 (Streamlit) | `Home.py`, `pages/*.py` | 互動式頁面：Campaign Cloner、Creative Uploader/Composer、Ad Set/Ad Creator。 |
| 公用層 | `src/utils/api_helpers.py`, `src/utils/ui_clipboard.py`, `src/utils/storage.py`, `src/utils/client.py`, `src/utils/config_loader.py` | API 重試、payload 淨化、UI clipboard、JSON 儲存、環境變數載入。 |
| 輸出資料 | `output/`, `output/insights/`, `output/inspector/` | RAW JSON 與報表輸出根目錄。 |

## 4. 主要資料流程
### 4.1 Data Lake (擷取與回填)
1. `load_config()` 讀取 `configs/config.yaml` → 解析 mode、ad_account_ids、date_range。
2. `get_api_client()` 從 `.env` 取得 `META_ACCESS_TOKEN`，初始化 `FacebookAdsApi`。
3. `main_extractor.py` 依 mode 決定 `filtering`（daily）或日期巡迴（backfill）。
4. 透過 `fetch_account_*`/`fetch_*_by_*` 組合 Graph API `FacebookRequest`，`execute_batch_requests()` 將請求分批執行：
   - 封裝成功、錯誤 callback，針對 rate limit（code/subcode）提供重試與退避。
   - 退避多次後改採序列呼叫，保留不可恢復項目於 log。
5. 以 `save_to_json()` 落地 JSON 至 `output/<YYYY-MM-DD>/`，檔名含 account id 提供追蹤。
6. Backfill 模式再逐日呼叫 `fetch_insights()`：非同步 job polling、取得 action_breakdowns、reach/frequency summary，儲存於 `output/insights/<date>/`。

### 4.2 行銷工具（GET → EDIT → POST → 淨化）
1. `pages/1_Campaign_Cloner.py` 透過 `campaign_inspector` 與 `objects_to_dict_list()` 將 Campaign 結構載入 Streamlit session。
2. 使用者於 EDIT 層覆寫欄位；`normalize_input_value()` 依 Schema 提示自動轉型（預算轉 int、JSON 欄位解析）。
3. `sanitize_*_payload()`（Campaign/Ad Set/Ad），結合模板與使用者輸入，區分 CBO 與非 CBO 預算欄位，補上預設狀態。
4. `sanitize_payload()`（`src/utils/api_helpers.py`）作為統一淨化層：
   - 預算欄位互斥、正整數化；
   - `special_ad_categories`、brand safety 欄位轉成 List；
   - Datetime 正規化成 ISO8601（UTC）；
   - 針對一般字串數值自動轉型為 int/float；
   - 過濾空值或不允許欄位。
5. `create_ad_object()` 呼叫 Meta API (`account.create_campaign/set/ad` 等)，並在 Streamlit/CLI 同步 log payload 與錯誤細節，提供除錯用的完整 JSON。
6. Creative toolchain：
   - Creative Uploader → 上傳影像/影片，等待 video ready，輸出 `image_hash` / `video_id` 至 clipboard。
   - Creative Composer → 支援單圖、Carousel、Collection、Raw JSON，產生可直接 POST 的 `object_story_spec`。
   - Ad/Ad Set Creator → 參照 `fields_schema` 提供欄位清單，沿用 sanitize → create 流程。

### 4.3 GET 工具補充
- `campaign_inspector.py` 跨層級抓取 Campaign → Ad Set → Ad → Creative，並將 Pixel 細節用 `enrich_ad_sets()` 加入每個 Ad Set。
- `get_pixels.py` 走訪 `config.yaml` 中的 account，輸出 `pixels_act_<id>.json` 以供追蹤。

## 5. 組態與機密
- `.env`：`META_ACCESS_TOKEN`，建議後續導入 Secret Manager 或 CI/CD runtime secret。
- `configs/config.yaml`：`mode`, `ad_account_ids`, `date_range`，可擴充 `fields`, `output_root` 等自訂參數。
- Schema 定義與 UI 欄位連動：`src/configs/fields_schema.py` 內含 GET/POST 欄位鍵集合，Streamlit 頁面直接引用以維持一致。

## 6. 資料輸出與契約
- `output/<date>/`：`campaigns_*.json`, `ad_sets_*.json`, `ads_*.json`, `creatives_*.json`，每筆為 Meta Graph API 回傳的原始欄位。
- `output/insights/<date>/`：每日 insights 拆成 action_type、ad/adset/campaign summary。
- `output/inspector/`：Campaign Inspector 報告，內含 campaign, ad_sets(含 ads), creatives, pixel_overview。
- RAW JSON 沒有 schema 版本管理；建議在 Pipeline 或後續 ETL 追加 schema snapshot 以避免欄位漂移。

## 7. 例外處理與觀測
- `make_api_request()`：針對 rate limit code/subcode 加入指數退避 + jitter，Account 限速（subcode 24460xx）延長為 10 分鐘以上。
- `execute_batch_requests()`：批次失敗自動重試，最終回退到逐筆執行，將失敗請求列印，方便手動補救。
- Streamlit 介面在 `create_ad_object()` 中直接顯示 Meta error payload，保留送出的 payload 供使用者複製。
- 尚未整合中央化 logging / telemetry；若要進入生產，建議補強。

## 8. 未完成項目與 Roadmap 建議
1. **Sanitize Layer 規則覆蓋率**：目前主要涵蓋預算、時間、brand safety、special_ad_categories；其餘欄位（如 bid_strategy 特殊值、catalog-based creative）仍需依實際案例擴充。
2. **欄位 schema 版本化**：`fields_schema.py` 為單檔常數；建議外部化到 YAML/JSON，並將版本資訊記錄在輸出的 RAW JSON 中。
3. **自動化測試**：缺乏單元/整合測試，可優先針對 `sanitize_payload()` 與批次擷取行為建立測試案例。
4. **錯誤通知**：目前僅 log，無警示；可串接 Slack/Email 或監控平台以便快速回應批次錯誤。
5. **部署流程**：排程、Docker 化與憑證管理尚待建立；可利用 GitHub Actions/Task Scheduler 配合虛擬環境。
6. **輸出清理策略**：`output/` 目前無生命周期管理，建議加入 retention 或上傳至雲端儲存（S3/Azure Blob）。

## 9. 開發者導覽
- **環境建置**：
  1. `python -m venv venv && venv\Scripts\activate`。
  2. `pip install -r requirements.txt`。
  3. 建立 `.env` 並填入 `META_ACCESS_TOKEN`。
- **執行 Data Lake 擷取**：`python src/main_extractor.py`；視需求調整 `configs/config.yaml` 的 mode 與日期。
- **擷取 Pixel**：`python src/extractors/get_pixels.py`。
- **啟動 Streamlit 工具**：`streamlit run Home.py`，選單可進入五個工具頁面。
- **程式碼閱讀順序建議**：
  1. `src/configs/fields_schema.py` → 理解欄位定義。
  2. `src/utils/api_helpers.py` → 掌握淨化與 API 重試邏輯。
  3. `src/main_extractor.py` → Data Lake 主程式流程。
  4. `pages/1_Campaign_Cloner.py` → GET/EDIT/POST 整合案例。

## 10. 文件索引
- `README.md`：快速啟動與操作指南。
- `docs/system-architecture.md`：原先的架構說明，可搭配本文件取得完整脈絡。
- 本文件：供新進開發者掌握技術細節與後續待辦。

