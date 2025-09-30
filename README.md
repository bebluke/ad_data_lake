# AD Data Lake MVP

## 專案簡介
本專案整合 Meta Facebook Marketing API 的資料擷取流程，以及協助OP團隊操作 Campaign、Ad Set、Ad 與 Creative 的 Streamlit 工具。核心目標是每日或批次匯出廣告帳戶資料至本地 Data Lake，並提供互動式界面快速複製與建立行銷資產。

## 核心功能
- **Data Extractor**：`src/main_extractor.py` 依據 `configs/config.yaml` 與 `.env` 驅動，支援 `daily` 與 `backfill` 模式，批次下載 Campaign、Ad Set、Ad、Creative 與 Insights。
- **Campaign Inspector**：`src/tools/campaign_inspector.py` 針對單一 Campaign 建立多層結構報告，整合像素與 Creative metadata。
- **Pixels Exporter**：`src/extractors/get_pixels.py` 依帳戶匯出 Pixel 清單。
- **Streamlit Marketing Ops Toolkit**：`Home.py` 與 `pages/` 底下頁面提供 Campaign Cloner、Creative Uploader、Creative Composer、Ad Set Creator、Ad Creator 等半自動化工作流程，並內建右側 Clipboard 功能。

## 專案結構
```
configs/           # YAML 設定檔
output/            # API 匯出資料的預設輸出目錄
pages/             # Streamlit 多頁工具
src/
  configs/         # 欄位對照與 schema 定義
  extractors/      # API 擷取與批次工具
  tools/           # 指令列分析工具
  utils/           # API 輔助函式、客戶端載入與 UI 共用元件
Home.py            # Streamlit 入口頁
requirements.txt   # Python 相依套件
```

## 系統需求
- Python 3.11 以上版本
- Meta Facebook Business SDK (`facebook_business`)
- Streamlit 1.31 以上（建議）
- 可存取 Facebook Marketing API 的 Access Token

## 安裝步驟
1. 建議於專案根目錄建立虛擬環境（可使用 `python -m venv venv`）。
2. 啟用虛擬環境後安裝套件：`pip install -r requirements.txt`。
3. 請勿將 `venv/`、`.env` 與 `output/` 目錄提交至 Git。

## 設定環境變數與參數
1. 在專案根目錄建立 `.env`，填入 `META_ACCESS_TOKEN=<你的 Meta Access Token>`。請妥善保護憑證，並避免提交版本控制。
2. 編輯 `configs/config.yaml`：
   - `mode`：`daily`（僅抓取更新資料）或 `backfill`（依日期範圍完整匯出）。
   - `ad_account_ids`：輸入目標帳戶 ID，可含 `act_` 前綴或純數字。
   - `date_range`：`backfill` 模式使用的日期區間。

## 執行方式
- **啟動 Streamlit 工具**：
  ```bash
  streamlit run Home.py
  ```
  啟動後可於瀏覽器存取：
  - Campaign Cloner：複製既有 Campaign/Ad Set/Ad 結構並修改主要欄位。
  - Creative Uploader：批次上傳圖像與影片，並同步顯示於 Clipboard。
  - Creative Composer：互動式產生單圖、Carousel、Collection 或 Raw JSON Creative。
  - Ad Set Creator / Ad Creator：依欄位 schema 引導建立新物件。

- **批次資料擷取**：
  ```bash
  python src/main_extractor.py
  ```
  依帳戶與模式寫出 JSON 於 `output/<YYYY-MM-DD>/`，包含 `campaigns_*.json`、`ad_sets_*.json`、`ads_*.json`、`creatives_*.json` 及 `insights_*` 子目錄。

- **像素清單匯出**：
  ```bash
  python src/extractors/get_pixels.py
  ```
  會於 `output/<當日日期>/` 產生 `pixels_act_<id>.json`。

- **Campaign Inspector 報告**：
  ```bash
  python src/tools/campaign_inspector.py --account act_<ACCOUNT_ID> --campaign <CAMPAIGN_ID> --output-dir output/inspector
  ```
  產生含 Campaign、Ad Set、Ad、Creative 與 Pixel 詳細資料的 JSON 報告。

## 測試與錯誤處理建議
- 發生 `rate limit` 時工具會自動以指數回退重試，必要時請調整 `configs/config.yaml` 中的帳戶清單或改用 `backfill` 模式分批擷取。
- 若 API 回應為空，請檢查 Access Token 權限與 `ad_account_ids` 是否正確。
- Streamlit 執行時若顯示無法載入素材，請確認已使用 Creative Uploader 或手動填入正確的 `image_hash`/`video_id`。

## 資料產出與後續流程
- `output/` 目錄可直接上傳至雲端儲存或 Data Lake，再由後續 ETL/ELT 管線載入。
- 建議搭配 GitHub Actions 或排程工具定時執行 `src/main_extractor.py`，並將資料推送至企業儲存端以利 BI 報表與增量分析。

## 延伸閱讀
- `docs/system-architecture.md`：原始架構概述。
- `docs/technical-spec.md`：完整技術規格、模組分層與待辦清單。
