# -*- coding: utf-8 -*-
from __future__ import annotations

import sys
from pathlib import Path
from typing import Any, Dict, List

import streamlit as st
from facebook_business.adobjects.adaccount import AdAccount
from facebook_business.adobjects.adset import AdSet
from facebook_business.adobjects.campaign import Campaign

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.configs.fields_schema import AD_POST_FIELDS
from src.extractors.api_extractor import objects_to_dict_list
from src.utils.api_helpers import create_ad_object, make_api_request
from src.utils.client import get_api_client
from src.utils.config_loader import load_config
from src.utils.ui_clipboard import ensure_asset_clipboard, render_asset_clipboard

ensure_asset_clipboard()
render_asset_clipboard()

st.title('廣告建立工具 (Ad Creator)')


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
    meta = AD_POST_FIELDS.get(field_name, {})
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


def fetch_ad_sets_for_campaign(api, campaign_id: str) -> List[Dict[str, Any]]:
    campaign = Campaign(campaign_id, api=api)
    params = {'limit': 200}
    cursor = make_api_request(
        lambda: campaign.get_ad_sets(
            fields=[AdSet.Field.id, AdSet.Field.name],
            params=params,
        )
    )
    if not cursor:
        return []

    ad_sets: List[Dict[str, Any]] = []
    while True:
        ad_sets.extend(objects_to_dict_list(cursor))
        has_next = make_api_request(cursor.load_next_page)
        if not has_next:
            break

    ad_sets.sort(key=lambda item: item.get('name') or '')
    return ad_sets


def get_cached_campaigns(api, account_id: str) -> List[Dict[str, Any]]:
    cache_key = f'ad_creator_campaigns_{account_id}'
    cached = st.session_state.get(cache_key)
    if cached is not None:
        return cached

    with st.spinner('載入 Campaign 資料中...'):
        campaigns = fetch_campaigns_for_account(api, account_id)

    st.session_state[cache_key] = campaigns
    return campaigns


def get_cached_ad_sets(api, campaign_id: str) -> List[Dict[str, Any]]:
    cache_key = f'ad_creator_adsets_{campaign_id}'
    cached = st.session_state.get(cache_key)
    if cached is not None:
        return cached

    with st.spinner('載入 Ad Set 資料中...'):
        ad_sets = fetch_ad_sets_for_campaign(api, campaign_id)

    st.session_state[cache_key] = ad_sets
    return ad_sets


def render_ad_form(account: AdAccount, ad_set: Dict[str, Any]) -> None:
    ad_set_id = str(ad_set.get('id') or '').strip()
    ad_set_name = (ad_set.get('name') or ad_set_id).strip()
    st.subheader(f'目標 Ad Set：{ad_set_name} ({ad_set_id})')

    with st.form('ad_creator_form'):
        form_values: Dict[str, str] = {}
        for field_name in AD_POST_FIELDS.keys():
            if field_name in {'adset_id', 'creative'}:
                continue

            label = get_field_label(field_name)
            widget_key = f'ad_creator_{field_name}_{ad_set_id}'

            if field_name == 'name':
                form_values[field_name] = st.text_input(label, key=widget_key)
            elif field_name == 'status':
                form_values[field_name] = st.selectbox(label, ['PAUSED', 'ACTIVE'], index=0, key=widget_key)
            else:
                form_values[field_name] = st.text_input(label, key=widget_key)

        creative_key = f'ad_creator_creative_id_{ad_set_id}'
        creative_input = st.text_input('廣告創意 ID (creative_id)', key=creative_key)
        st.caption('請從左側剪貼簿或 Creative Composer 頁面複製 creative_id。')

        submit = st.form_submit_button('建立 Ad')

    if not submit:
        return

    payload: Dict[str, Any] = {'adset_id': ad_set_id}
    for field_name, value in form_values.items():
        if isinstance(value, str):
            trimmed = value.strip()
            if trimmed:
                payload[field_name] = trimmed

    creative_id = (creative_input or '').strip()
    if not creative_id:
        st.error('請輸入 creative_id 後再提交。')
        return

    payload['creative'] = {'creative_id': creative_id}

    result = create_ad_object(
        creation_function=lambda: account.create_ad(params=payload),
        payload=payload,
        object_type_for_log='Ad',
    )

    if not result:
        return

    result_data = object_to_dict(result)
    st.success('Ad 建立成功！')
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

    default_account = st.session_state.get('ad_creator_account_id')
    account_index = account_options.index(default_account) if default_account in account_options else 0
    account_id = st.selectbox('選擇廣告帳戶', account_options, index=account_index, key='ad_creator_account_select')
    st.session_state.ad_creator_account_id = account_id

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

    default_campaign_id = st.session_state.get('ad_creator_campaign_id')
    default_campaign_index = 0
    if default_campaign_id:
        for idx, item in enumerate(campaigns):
            if str(item.get('id')) == str(default_campaign_id):
                default_campaign_index = idx
                break

    selected_campaign = st.selectbox(
        '選擇 Campaign',
        campaigns,
        index=default_campaign_index,
        format_func=lambda item: f"{item.get('name') or item.get('id')} ({item.get('id')})",
        key='ad_creator_campaign_select',
    )

    if not selected_campaign:
        st.info('請先選擇 Campaign。')
        return

    campaign_id = str(selected_campaign.get('id') or '')
    st.session_state.ad_creator_campaign_id = campaign_id

    ad_sets = get_cached_ad_sets(api, campaign_id)
    if not ad_sets:
        st.info('此 Campaign 尚未擁有任何 Ad Set。')
        return

    default_adset_id = st.session_state.get('ad_creator_adset_id')
    default_adset_index = 0
    if default_adset_id:
        for idx, item in enumerate(ad_sets):
            if str(item.get('id')) == str(default_adset_id):
                default_adset_index = idx
                break

    selected_ad_set = st.selectbox(
        '選擇 Ad Set',
        ad_sets,
        index=default_adset_index,
        format_func=lambda item: f"{item.get('name') or item.get('id')} ({item.get('id')})",
        key='ad_creator_adset_select',
    )

    if not selected_ad_set:
        st.info('請先選擇 Ad Set。')
        return

    st.session_state.ad_creator_adset_id = selected_ad_set.get('id')

    render_ad_form(account, selected_ad_set)


if __name__ == '__main__':
    main()
