from pathlib import Path
import sys

import streamlit as st

PROJECT_ROOT = Path(__file__).resolve().parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.utils.ui_clipboard import ensure_asset_clipboard, render_asset_clipboard

st.set_page_config(page_title="Marketing Ops Toolkit", layout="wide")

ensure_asset_clipboard()
render_asset_clipboard()

st.title("Campaigns Toolkit")
st.info("請選擇左側工具開始使用.")
