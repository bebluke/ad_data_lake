# -*- coding: utf-8 -*-
from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

import streamlit as st
from facebook_business.adobjects.adaccount import AdAccount
from facebook_business.adobjects.campaign import Campaign

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.configs.fields_schema import ADSET_POST_FIELDS
from src.extractors.api_extractor import objects_to_dict_list
from src.utils.api_helpers import create_ad_object, make_api_request
from src.utils.client import get_api_client
from src.utils.config_loader import load_config
from src.utils.ui_clipboard import ensure_asset_clipboard, render_asset_clipboard

ensure_asset_clipboard()
render_asset_clipboard()

st.title('廣告組合建立工具 (Ad Set Creator)')


def object_to_dict(obj: Any) -> Dict[str, Any]:
    if isinstance(obj, dict):
        return obj
    exporter = getattr(obj, 'export_all_data', None)
    if callable(exporter):
        data = exporter()
        if isinstance(data, dict):
            return data
    return {}


def get_field_label(field_name: str) -> str:
    meta = ADSET_POST_FIELDS.get(field_name, {})
    zh_tw = meta.get('zh_tw', field_name)
    return f"{zh_tw} ({field_name})"


def fetch_campaigns_for_account(api, account_id: str) -> List[Dict[str, Any]]:
    account = AdAccount(account_id, api=api)
    params = {'limit': 200}
    cursor = make_api_request(
        lambda: account.get_campaigns(
            fields=[Campaign.Field.id, Campaign.Field.name],
            params=params,
        )
    )
    if not cursor:
        return []

    campaigns: List[Dict[str, Any]] = []
    while True:
        campaigns.extend(objects_to_dict_list(cursor))
        has_next = make_api_request(cursor.load_next_page)
        if not has_next:
            break

    campaigns.sort(key=lambda item: item.get('name') or '')
    return campaigns


def get_cached_campaigns(api, account_id: str) -> List[Dict[str, Any]]:
    cache_key = f'adset_creator_campaigns_{account_id}'
    cached = st.session_state.get(cache_key)
    if cached is not None:
        return cached

    with st.spinner('載入 Campaign 資料中...'):
        campaigns = fetch_campaigns_for_account(api, account_id)

    st.session_state[cache_key] = campaigns
    return campaigns


def parse_targeting(value: Any) -> Optional[Dict[str, Any]]:
    if isinstance(value, dict):
        return value
    if hasattr(value, 'to_dict'):
        try:
            converted = value.to_dict()
        except Exception:
            converted = None
        if isinstance(converted, dict):
            return converted
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return None
        try:
            parsed = json.loads(text)
        except json.JSONDecodeError:
            return None
        return parsed if isinstance(parsed, dict) else None
    return None


def render_ad_set_form(account: AdAccount, campaign: Dict[str, Any]) -> None:
    campaign_id = str(campaign.get('id') or '').strip()
    campaign_name = (campaign.get('name') or campaign_id).strip()
    st.subheader(f'目標 Campaign：{campaign_name} ({campaign_id})')

    with st.form('adset_creator_form'):
        form_values: Dict[str, Any] = {}
        for field_name in ADSET_POST_FIELDS.keys():
            if field_name == 'campaign_id':
                continue

            label = get_field_label(field_name)
            widget_key = f'adset_{field_name}'

            if field_name == 'name':
                form_values[field_name] = st.text_input(label, key=widget_key)
            elif field_name == 'status':
                options = ['PAUSED', 'ACTIVE']
                form_values[field_name] = st.selectbox(label, options, index=0, key=widget_key)
            elif field_name in {'daily_budget', 'lifetime_budget'}:
                form_values[field_name] = st.number_input(label, min_value=0, value=0, step=1, key=widget_key)
            elif field_name == 'targeting':
                form_values[field_name] = st.data_editor({}, key=widget_key, use_container_width=True, num_rows='dynamic')
            else:
                form_values[field_name] = st.text_input(label, key=widget_key)

        submit = st.form_submit_button('建立 Ad Set')

    if not submit:
        return

    payload: Dict[str, Any] = {'campaign_id': campaign_id}
    for field_name, value in form_values.items():
        if field_name in {'daily_budget', 'lifetime_budget'}:
            if isinstance(value, (int, float)) and value > 0:
                payload[field_name] = int(value)
        elif field_name == 'targeting':
            targeting_value = parse_targeting(value)
            if targeting_value is not None:
                payload[field_name] = targeting_value
        else:
            if isinstance(value, str):
                trimmed = value.strip()
                if trimmed:
                    payload[field_name] = trimmed
            elif value not in (None, ''):
                payload[field_name] = value

    result = create_ad_object(
        creation_function=lambda: account.create_ad_set(params=payload),
        payload=payload,
        object_type_for_log='Ad Set',
    )

    if not result:
        return

    result_data = object_to_dict(result)
    st.success('Ad Set 建立成功！')
    if result_data:
        st.json(result_data)
    else:
        st.write(result)


def main() -> None:
    try:
        config = load_config()
    except Exception as exc:  # pragma: no cover - defensive for UI runtime
        st.error(f'載入 config.yaml 失敗：{exc}')
        return

    account_options: List[str] = config.get('ad_account_ids') or []
    if not account_options:
        st.warning('config.yaml 尚未設定任何 ad_account_ids。')
        return

    default_account = st.session_state.get('adset_creator_account_id')
    account_index = account_options.index(default_account) if default_account in account_options else 0
    account_id = st.selectbox('選擇廣告帳戶', account_options, index=account_index, key='adset_creator_account_select')
    st.session_state.adset_creator_account_id = account_id

    try:
        api = get_api_client()
    except Exception as exc:  # pragma: no cover - defensive for UI runtime
        st.error(f'初始化 Meta API 客戶端失敗：{exc}')
        return

    account = AdAccount(account_id, api=api)
    campaigns = get_cached_campaigns(api, account_id)
    if not campaigns:
        st.info('此廣告帳戶目前沒有可用的 Campaign。')
        return

    default_campaign_id = st.session_state.get('adset_creator_campaign_id')
    default_index = 0
    if default_campaign_id:
        for idx, item in enumerate(campaigns):
            if str(item.get('id')) == str(default_campaign_id):
                default_index = idx
                break

    selected_campaign = st.selectbox(
        '選擇 Campaign',
        campaigns,
        index=default_index,
        format_func=lambda item: f"{item.get('name') or item.get('id')} ({item.get('id')})",
        key='adset_creator_campaign_select',
    )

    if not selected_campaign:
        st.info('請選擇一個 Campaign 以繼續。')
        return

    st.session_state.adset_creator_campaign_id = selected_campaign.get('id')

    render_ad_set_form(account, selected_campaign)


if __name__ == '__main__':
    main()
