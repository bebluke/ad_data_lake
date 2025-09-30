# 系統架構索引（AD Data Lake MVP）

## 1. 系統總覽
本系統面向OP團隊，整合 Meta Facebook Marketing API 的資料抽取、資料儲存與半自動化操作工具。後端以 Python 撰寫，透過 `.env` 取得 `Meta Access Token`，搭配 `configs/config.yaml` 定義執行模式與帳戶清單；前端則利用 Streamlit 提供管理介面。所有匯出結果預設寫入 `output/` 目錄，作為後續 Data Lake 或報表的輸入。

## 2. 資料流程
1. **設定階段**：使用者維護 `configs/config.yaml`（模式、帳戶、日期），並在 `.env` 設定 `META_ACCESS_TOKEN`。
2. **授權與初始化**：`src/utils/client.py#get_api_client` 透過 dotenv 載入 Access Token，初始化 `FacebookAdsApi`。
3. **資料抽取**：`src/main_extractor.py#L37` 起始主流程，依模式呼叫 `AdAccount` API 取得 Campaign → Ad Set → Ad → Creative，並根據需要執行 Insights 查詢。
4. **批次與錯誤處理**：`execute_batch_requests`、`make_api_request` 共同處理批次請求、指數回退與 `rate limit`。
5. **儲存輸出**：`save_to_json`／`src/utils/storage.py#L8` 將結果寫入 `output/<日期>/`。
6. **互動工具**：使用者可透過 Streamlit (`Home.py` 與 `pages/`) 進行 Campaign 複製、素材上傳、新建 Ad Set / Ad、組合 Creative 等操作。
7. **分析工具**：`src/tools/campaign_inspector.py` 與 `src/extractors/get_pixels.py` 提供指令列分析與 Pixel 匯出輔助。

## 3. 模組分層
### 3.1 設定與密鑰管理
- `configs/config.yaml`：定義執行模式 (`daily` / `backfill`)、帳戶列表與日期範圍；`load_config` 會以 `utf-8-sig` 讀取避免 BOM。
- `.env`：儲存敏感的 `META_ACCESS_TOKEN`。`src/utils/client.py` 於執行初期載入並驗證是否有給值。

### 3.2 核心資料抽取（`src/main_extractor.py`）
- `create_graph_params`、`collect_cursor_data`：統一產生 Graph API 參數並轉換游標結果。
- `fetch_account_ad_sets`、`fetch_ad_sets_by_campaigns`：先嘗試帳戶層級 API，失敗時以 `FacebookRequest` 組批次請求補抓。
- `fetch_account_ads`、`fetch_ads_by_adsets`：與 Ad Set 相同策略，優先使用帳戶 API，再以批次補齊。
- `execute_batch_requests`：包裝 `api.new_batch()`，針對 `rate limit` 設計 `max_chunk_retries`、指數回退與 `jitter`，並以回呼處理成功／失敗案例。
- `handle_adset_batch_response`、`handle_ads_batch_response`：整理回傳結構，確保批次回傳的物件包含對應 ID。
- `main()`：
  1. 載入設定與欄位 schema (`fields_schema.py`)，決定抓取欄位。
  2. 逐一帳戶抓取 Campaign、Ad Set、Ad；整理 Creative ID。
  3. 以批次方式補抓 Creative 細節並保持去重。
  4. `backfill` 模式下逐日呼叫 `fetch_insights` 擷取 `action_breakdowns`、`reach & frequency` 等統計，輸出至 `insights/<日期>/`。
  5. 所有資料使用 `save_to_json` 輸出，並在流程中加入人工等待避免 API 過載。

### 3.3 共用函式庫（`src/utils`）
- `api_helpers.py`：
  - `make_api_request`：核心重試機制，針對 `RATE_LIMIT_ERROR_CODES` 及 `ACCOUNT_RATE_LIMIT_SUBCODES` 進行指數回退、隨機抖動。
  - `sanitize_payload`：清理 POST payload，將 `daily_budget` 等欄位轉換為整數，並處理空值、JSON 字串。
  - `create_ad_object`：結合 `sanitize_payload`、log 與錯誤訊息顯示，Streamlit 介面與 CLI 均共用。
  - 其餘輔助包含錯誤訊息解析、日期格式正規化與 Streamlit 互動訊息。
- `config_loader.py`：讀取 YAML 設定並回傳 dict，提供 Streamlit 與 CLI 共用。
- `storage.py`：`save_to_json` 封裝 JSON 儲存邏輯，確保目錄存在。
- `ui_clipboard.py`：在 Streamlit session 中維護 `asset_clipboard`，提供右側 Clipboard 面板與樣式；多個頁面依賴 `ensure_asset_clipboard` 與 `render_asset_clipboard`。

### 3.4 API 抽取輔助（`src/extractors`）
- `api_extractor.py`：
  - `objects_to_dict_list`：處理 SDK 物件轉換為 dict，避免游標耗盡。
  - `fetch_insights`：以 `is_async=True` 觸發 Insights，輪詢 `AdReportRun` 直到完成；支援 `breakdowns`、`action_breakdowns`。
  - `fetch_creatives_by_ids`：序列化抓取 Creative metadata，保留遺失清單供診斷。
- `get_pixels.py`：讀取帳戶列表後為每個帳戶呼叫 `AdAccount.get_ad_pixels`，透過 `save_to_json` 寫入 `output/<日期>/`，並輸出帳戶與像素數量簡報。

### 3.5 Streamlit 工具（`Home.py` 與 `pages/`）
- `Home.py`：設定 Streamlit page config，載入 Clipboard，提供入口提示。
- `pages/1_Campaign_Cloner.py`：
  - 下載指定 Campaign 層級資料，使用者可在 UI 中調整欄位，並透過 `create_ad_object` 依序建立 Campaign → Ad Set → Ad。
  - 定義欄位常數（例如 `BUDGET_FIELDS`、`JSON_FIELD_HINTS`）確保欄位轉換一致。
  - 支援檔案上傳並匹配 Creative 新資產。
- `pages/2_Creative_Uploader.py`：
  - 根據檔案類型決定使用 `AdImage` 或 `AdVideo` API，影片上傳後透過 `wait_for_video_ready` 輪詢狀態。
  - 成功上傳後將 `image_hash` 或 `video_id` 寫入 Clipboard，供其他頁面引用。
- `pages/3_Creative_Composer.py`：
  - 提供多種 Creative 模板（單圖、Carousel、Collection、Raw JSON）。
  - 若選擇 Catalog 相關格式，會呼叫 `ProductCatalog` API 取得 Catalog 與 Product Set 列表。
  - 最終 payload 透過 `create_ad_object` 建立並回寫結果。
- `pages/4_AdSet_Creator.py`：
  - 根據 `ADSET_POST_FIELDS` 動態渲染表單，支援 JSON 欄位輸入與目標設定解析。
  - 透過 `fetch_campaigns_for_account` 快取帳戶 Campaign 清單。
- `pages/5_Ad_Creator.py`：
  - 先選擇 Campaign → Ad Set，再依 `AD_POST_FIELDS` 輸入欄位並引用既有 Creative ID。
  - 使用 `create_ad_object` 建立 Ad，並提示使用者從 Clipboard 貼入 Creative ID。

### 3.6 指令列工具
- `src/tools/campaign_inspector.py`：結合 `fetch_ad_sets`、`fetch_ads` 與 `fetch_creatives`，輸出深度結構化報告；同時整合像素細節（透過 `fetch_pixels_for_account`）與 Creative metadata。
- 所有工具透過 `objects_to_dict_list` 與 `make_api_request` 共享錯誤處理與游標邏輯。

## 4. 輸出與檔案命名
- `output/<YYYY-MM-DD>/`：主要資料匯出目錄。
  - `campaigns_act_<id>.json`、`ad_sets_act_<id>.json`、`ads_act_<id>.json`、`creatives_act_<id>.json`
  - `insights/<日期>/`：依日彙整 `insights_action_type_*`、`insights_ad_summary_*` 等檔案。
- `output/inspector/`：Campaign Inspector 報告。
- 建議使用 Git ignore 排除大檔案；可搭配雲端同步或 S3 作為後續 Data Lake 來源。

## 5. 排程與監控建議
- **排程**：可使用 Windows Task Scheduler、cron 或 GitHub Actions 定時執行 `python src/main_extractor.py`，每日將結果上傳至儲存系統。
- **監控**：
  - 監看 `rate limit` log；必要時延長 `pause_seconds` 或減少 `chunk_size`。
  - 記錄 `fetch_creatives_by_ids` 回傳的遺失清單，確認素材是否已刪除或權限不足。
  - Streamlit 介面可加入額外提示（如 `st.toast`）回報 API 失敗狀態。

## 6. 擴充與維護指引
- **新增欄位**：於 `src/configs/fields_schema.py` 補充欄位後，`main_extractor` 與 Streamlit 表單會自動反映（基於 key 列表）。若屬於 POST payload，需同時更新 `*_POST_FIELDS`。
- **支援新資料集**：在 `src/extractors` 新增模組，並於 `main_extractor` 或獨立腳本呼叫；請共用 `make_api_request` 以保持一致錯誤處理。
- **整合外部儲存**：可在 `src/utils/storage.py` 新增寫入 S3、Azure Blob 等函式，於 `main_extractor` 或工具中切換。
- **自動化測試**：建議為新增模組撰寫 `pytest`，特別是 payload sanitization 與錯誤處理邏輯，避免 API 更新造成回歸問題。
- **安全性**：Access Token 建議透過 Secret Manager／CI/CD 環境變數注入，不應硬碼或提交到 repository。