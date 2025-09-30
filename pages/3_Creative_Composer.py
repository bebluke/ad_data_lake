from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

import streamlit as st
from facebook_business.adobjects.adaccount import AdAccount
from facebook_business.adobjects.productcatalog import ProductCatalog

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.utils.api_helpers import create_ad_object, make_api_request
from src.utils.client import get_api_client
from src.utils.config_loader import load_config
from src.utils.ui_clipboard import ensure_asset_clipboard, render_asset_clipboard

ensure_asset_clipboard()
render_asset_clipboard()
CREATIVE_FORMAT_OPTIONS = [
    '單一圖片/影片',
    '輪播 (Carousel)',
    '精品欄 (Collection)',
    '進階模式 (Raw JSON)',
]

CTA_DEFAULT = 'LEARN_MORE'
DEFAULT_RAW_SPEC: Dict[str, Any] = {'object_story_spec': {'page_id': '', 'link_data': {}}}


def object_to_dict(obj: Any) -> Dict[str, Any]:
    if isinstance(obj, dict):
        return obj
    exporter = getattr(obj, 'export_all_data', None)
    if callable(exporter):
        data = exporter()
        if isinstance(data, dict):
            return data
    return {}


def build_call_to_action(cta_type: str, link: str) -> Optional[Dict[str, Any]]:
    cta_value = (cta_type or '').strip().upper()
    if not cta_value:
        return None
    value: Dict[str, Any] = {}
    link_value = (link or '').strip()
    if link_value:
        value['link'] = link_value
    payload: Dict[str, Any] = {'type': cta_value}
    if value:
        payload['value'] = value
    return payload


def fetch_product_catalogs(account: AdAccount) -> List[Dict[str, str]]:
    api = account.get_api()
    account_id = account.get_id_assured()
    response = make_api_request(lambda: api.call('GET', [account_id, 'product_catalogs'], params={'fields': 'id,name'}))
    if not response:
        return []
    data = response.json() if hasattr(response, 'json') else {}
    catalog_entries = data.get('data', []) if isinstance(data, dict) else []
    catalogs: List[Dict[str, str]] = []
    for entry in catalog_entries:
        if not isinstance(entry, dict):
            continue
        catalog_id = str(entry.get('id') or '').strip()
        if not catalog_id:
            continue
        name = (entry.get('name') or catalog_id).strip()
        catalogs.append({'id': catalog_id, 'name': name})
    return sorted(catalogs, key=lambda item: item['name'])


def fetch_product_sets(account: AdAccount, catalog_id: str) -> List[Dict[str, str]]:
    if not catalog_id:
        return []
    catalog = ProductCatalog(catalog_id, api=account.get_api())
    response = make_api_request(lambda: catalog.get_product_sets(fields=['id', 'name']))
    if not response:
        return []
    product_sets: List[Dict[str, str]] = []
    for product_set in response:
        data = object_to_dict(product_set)
        set_id = str(data.get('id') or '').strip()
        if not set_id:
            continue
        name = (data.get('name') or set_id).strip()
        product_sets.append({'id': set_id, 'name': name})
    return sorted(product_sets, key=lambda item: item['name'])


def get_cached_catalogs(account: AdAccount) -> List[Dict[str, str]]:
    cache_key = f'catalogs_{account.get_id()}'
    cached = st.session_state.get(cache_key)
    if cached is not None:
        return cached
    with st.spinner('載入商品目錄中...'):
        catalogs = fetch_product_catalogs(account)
    st.session_state[cache_key] = catalogs
    return catalogs


def get_cached_product_sets(account: AdAccount, catalog_id: str) -> List[Dict[str, str]]:
    if not catalog_id:
        return []
    cache_key = f'product_sets_{account.get_id()}_{catalog_id}'
    cached = st.session_state.get(cache_key)
    if cached is not None:
        return cached
    with st.spinner('載入商品組合中...'):
        product_sets = fetch_product_sets(account, catalog_id)
    st.session_state[cache_key] = product_sets
    return product_sets


def ensure_carousel_cards_initialized() -> None:
    if 'carousel_card_ids' not in st.session_state or not st.session_state.carousel_card_ids:
        st.session_state.carousel_card_ids = [1]


def add_carousel_card() -> None:
    ensure_carousel_cards_initialized()
    next_id = max(st.session_state.carousel_card_ids) + 1
    st.session_state.carousel_card_ids.append(next_id)


def remove_carousel_card() -> None:
    ensure_carousel_cards_initialized()
    if len(st.session_state.carousel_card_ids) <= 1:
        return
    removed_id = st.session_state.carousel_card_ids.pop()
    for field in ('headline', 'link', 'image_hash'):
        st.session_state.pop(f'carousel_card_{removed_id}_{field}', None)




def create_creative(account: AdAccount, params: Dict[str, Any]) -> str:
    creative_name = ''
    if isinstance(params, dict):
        creative_name = (params.get('name') or '').strip()
    label = f"Creative: {creative_name}" if creative_name else 'Creative'

    response = create_ad_object(
        creation_function=lambda: account.create_ad_creative(params=params),
        payload=params,
        object_type_for_log=label,
    )
    if not response:
        raise RuntimeError('建立 Creative 失敗，請確認輸入內容。')

    data = object_to_dict(response)
    creative_id = data.get('id')
    if not creative_id:
        getter = getattr(response, 'get', None)
        if callable(getter):
            try:
                creative_id = getter('id')
            except Exception:
                creative_id = None
    if not creative_id:
        getter = getattr(response, 'get_id', None)
        if callable(getter):
            creative_id = getter()
    if not creative_id:
        raise RuntimeError('Creative 已建立但 API 未回傳 ID。')
    return str(creative_id)


def assemble_single(
    page_id: str,
    creative_name: str,
    message: str,
    headline: str,
    link: str,
    cta_type: str,
    image_hash: str,
    video_id: str,
) -> Dict[str, Any]:
    page_value = (page_id or '').strip()
    if not page_value:
        raise ValueError('Page ID 為必填欄位。')
    link_value = (link or '').strip()
    if not link_value:
        raise ValueError('連結 URL 為必填欄位。')
    image_hash_value = (image_hash or '').strip()
    video_id_value = (video_id or '').strip()
    if not image_hash_value and not video_id_value:
        raise ValueError('請填寫 image_hash 或 video_id。')
    message_value = (message or '').strip()
    headline_value = (headline or '').strip()

    link_data: Dict[str, Any] = {'link': link_value}
    if message_value:
        link_data['message'] = message_value
    if headline_value:
        link_data['name'] = headline_value
    cta = build_call_to_action(cta_type, link_value)
    if cta:
        link_data['call_to_action'] = cta
    if image_hash_value:
        link_data['image_hash'] = image_hash_value
    if video_id_value:
        link_data['video_id'] = video_id_value

    payload: Dict[str, Any] = {'object_story_spec': {'page_id': page_value, 'link_data': link_data}}
    if creative_name:
        payload['name'] = creative_name.strip()
    if message_value:
        payload['body'] = message_value
    if headline_value:
        payload['title'] = headline_value
    return payload


def assemble_carousel(
    page_id: str,
    creative_name: str,
    message: str,
    headline: str,
    link: str,
    cta_type: str,
    cards: List[Dict[str, str]],
) -> Dict[str, Any]:
    page_value = (page_id or '').strip()
    if not page_value:
        raise ValueError('Page ID 為必填欄位。')
    link_value = (link or '').strip()
    if not link_value:
        raise ValueError('輪播需要主要連結 URL。')
    if not cards:
        raise ValueError('至少需要一張輪播卡片。')
    message_value = (message or '').strip()
    headline_value = (headline or '').strip()

    child_attachments: List[Dict[str, Any]] = []
    for index, card in enumerate(cards, start=1):
        card_headline = (card.get('headline') or '').strip()
        card_link = (card.get('link') or '').strip() or link_value
        image_hash_value = (card.get('image_hash') or '').strip()
        if not image_hash_value:
            raise ValueError(f'輪播卡片 {index} 缺少 image_hash。')
        attachment = {
            'link': card_link,
            'name': card_headline or f'卡片 {index}',
            'image_hash': image_hash_value,
        }
        child_attachments.append(attachment)

    link_data: Dict[str, Any] = {
        'link': link_value,
        'child_attachments': child_attachments,
    }
    if message_value:
        link_data['message'] = message_value
    if headline_value:
        link_data['name'] = headline_value
    cta = build_call_to_action(cta_type, link_value)
    if cta:
        link_data['call_to_action'] = cta

    payload: Dict[str, Any] = {'object_story_spec': {'page_id': page_value, 'link_data': link_data}}
    if creative_name:
        payload['name'] = creative_name.strip()
    if message_value:
        payload['body'] = message_value
    if headline_value:
        payload['title'] = headline_value
    return payload


def assemble_collection(
    page_id: str,
    creative_name: str,
    message: str,
    headline: str,
    link: str,
    cta_type: str,
    image_hash: str,
    video_id: str,
    product_set_id: str,
) -> Dict[str, Any]:
    page_value = (page_id or '').strip()
    if not page_value:
        raise ValueError('Page ID 為必填欄位。')
    product_set_value = (product_set_id or '').strip()
    if not product_set_value:
        raise ValueError('請選擇商品組合。')
    link_value = (link or '').strip()
    if not link_value:
        raise ValueError('Collection Creative 需要連結 URL。')
    image_hash_value = (image_hash or '').strip()
    video_id_value = (video_id or '').strip()
    if not image_hash_value and not video_id_value:
        raise ValueError('請填寫封面素材的 image_hash 或 video_id。')
    message_value = (message or '').strip()
    headline_value = (headline or '').strip()

    template_data: Dict[str, Any] = {
        'product_set_id': product_set_value,
        'link': link_value,
    }
    if message_value:
        template_data['message'] = message_value
    if headline_value:
        template_data['name'] = headline_value
    if image_hash_value:
        template_data['image_hash'] = image_hash_value
    if video_id_value:
        template_data['video_id'] = video_id_value
    cta = build_call_to_action(cta_type, link_value)
    if cta:
        template_data['call_to_action'] = cta

    payload: Dict[str, Any] = {'object_story_spec': {'page_id': page_value, 'template_data': template_data}}
    if creative_name:
        payload['name'] = creative_name.strip()
    if message_value:
        payload['body'] = message_value
    if headline_value:
        payload['title'] = headline_value
    return payload


def assemble_raw_payload(raw_data: Any, creative_name: str) -> Dict[str, Any]:
    if not isinstance(raw_data, dict):
        raise ValueError('Raw JSON 內容必須為物件。')
    payload = json.loads(json.dumps(raw_data))
    story_spec = payload.get('object_story_spec')
    if not isinstance(story_spec, dict):
        raise ValueError('Raw JSON 需要包含 object_story_spec，且必須為物件。')
    if creative_name:
        payload['name'] = creative_name.strip()
    return payload


def render_single_form(account: AdAccount, page_id: str) -> None:
    st.session_state.setdefault('single_cta_type', CTA_DEFAULT)
    with st.form('single_creative_form'):
        creative_name = st.text_input('Creative 名稱 (可選)', key='single_creative_name')
        message = st.text_area('主要文案', key='single_message')
        headline = st.text_input('標題', key='single_headline')
        link = st.text_input('連結 URL', key='single_link')
        image_hash = st.text_input('image_hash', key='single_image_hash')
        video_id = st.text_input('video_id (選填)', key='single_video_id')
        cta_type = st.text_input('CTA 類型', key='single_cta_type')
        submitted = st.form_submit_button('建立 Creative')
    if submitted:
        try:
            payload = assemble_single(page_id, creative_name, message, headline, link, cta_type, image_hash, video_id)
            with st.spinner('建立 Creative 中...'):
                creative_id = create_creative(account, payload)
            st.success(f'Creative 建立成功：{creative_id}')
        except Exception as exc:
            st.error(str(exc))


def render_carousel_section(account: AdAccount, page_id: str) -> None:
    ensure_carousel_cards_initialized()
    cols = st.columns(2)
    if cols[0].button('新增卡片', key='carousel_add_card'):
        add_carousel_card()
    if cols[1].button('刪除最後一張', key='carousel_remove_card'):
        remove_carousel_card()

    st.session_state.setdefault('carousel_cta_type', CTA_DEFAULT)
    with st.form('carousel_creative_form'):
        creative_name = st.text_input('Creative 名稱 (可選)', key='carousel_creative_name')
        message = st.text_area('整體文案', key='carousel_message')
        headline = st.text_input('主標題 (可選)', key='carousel_headline')
        link = st.text_input('輪播主連結 URL', key='carousel_link')
        cta_type = st.text_input('CTA 類型', key='carousel_cta_type')

        cards: List[Dict[str, str]] = []
        for index, card_id in enumerate(st.session_state.carousel_card_ids, start=1):
            st.markdown(f'**卡片 {index}**')
            key_prefix = f'carousel_card_{card_id}'
            st.session_state.setdefault(f'{key_prefix}_headline', '')
            st.session_state.setdefault(f'{key_prefix}_link', '')
            st.session_state.setdefault(f'{key_prefix}_image_hash', '')
            card_headline = st.text_input('卡片標題', key=f'{key_prefix}_headline')
            card_link = st.text_input('卡片連結 (可留空沿用主連結)', key=f'{key_prefix}_link')
            card_image_hash = st.text_input('卡片 image_hash', key=f'{key_prefix}_image_hash')
            cards.append({'headline': card_headline, 'link': card_link, 'image_hash': card_image_hash})

        submitted = st.form_submit_button('建立 Creative')

    if submitted:
        try:
            payload = assemble_carousel(page_id, creative_name, message, headline, link, cta_type, cards)
            with st.spinner('建立 Creative 中...'):
                creative_id = create_creative(account, payload)
            st.success(f'Creative 建立成功：{creative_id}')
        except Exception as exc:
            st.error(str(exc))


def render_collection_section(account: AdAccount, page_id: str) -> None:
    catalogs = get_cached_catalogs(account)
    if not catalogs:
        st.info('此帳戶暫無商品目錄可供選擇。')
        return

    selected_catalog_id = st.session_state.get('collection_selected_catalog')
    try:
        catalog_index = next(i for i, item in enumerate(catalogs) if item['id'] == selected_catalog_id)
    except StopIteration:
        catalog_index = 0

    selected_catalog = st.selectbox(
        '選擇商品目錄',
        catalogs,
        index=catalog_index,
        format_func=lambda item: f"{item['name']} ({item['id']})",
    )
    st.session_state.collection_selected_catalog = selected_catalog['id']

    product_sets = get_cached_product_sets(account, selected_catalog['id'])
    if not product_sets:
        st.info('所選商品目錄尚未建立商品組合。')
        return

    selected_product_set_id = st.session_state.get('collection_selected_product_set')
    try:
        product_set_index = next(i for i, item in enumerate(product_sets) if item['id'] == selected_product_set_id)
    except StopIteration:
        product_set_index = 0

    selected_product_set = st.selectbox(
        '選擇商品組合',
        product_sets,
        index=product_set_index,
        format_func=lambda item: f"{item['name']} ({item['id']})",
    )
    st.session_state.collection_selected_product_set = selected_product_set['id']

    st.session_state.setdefault('collection_cta_type', CTA_DEFAULT)
    with st.form('collection_creative_form'):
        creative_name = st.text_input('Creative 名稱 (可選)', key='collection_creative_name')
        message = st.text_area('整體文案', key='collection_message')
        headline = st.text_input('標題', key='collection_headline')
        link = st.text_input('連結 URL', key='collection_link')
        image_hash = st.text_input('封面 image_hash', key='collection_image_hash')
        video_id = st.text_input('封面 video_id (選填)', key='collection_video_id')
        cta_type = st.text_input('CTA 類型', key='collection_cta_type')
        submitted = st.form_submit_button('建立 Creative')

    if submitted:
        try:
            payload = assemble_collection(
                page_id,
                creative_name,
                message,
                headline,
                link,
                cta_type,
                image_hash,
                video_id,
                selected_product_set['id'],
            )
            with st.spinner('建立 Creative 中...'):
                creative_id = create_creative(account, payload)
            st.success(f'Creative 建立成功：{creative_id}')
        except Exception as exc:
            st.error(str(exc))


def render_raw_section(account: AdAccount) -> None:
    if 'raw_story_spec_data' not in st.session_state:
        st.session_state.raw_story_spec_data = json.loads(json.dumps(DEFAULT_RAW_SPEC))

    with st.form('raw_creative_form'):
        creative_name = st.text_input('Creative 名稱 (可選)', key='raw_creative_name')
        raw_data = st.data_editor(
            st.session_state.raw_story_spec_data,
            key='raw_story_spec_editor',
        )
        submitted = st.form_submit_button('建立 Creative')

    if submitted:
        try:
            st.session_state.raw_story_spec_data = raw_data
            payload = assemble_raw_payload(raw_data, creative_name)
            with st.spinner('建立 Creative 中...'):
                creative_id = create_creative(account, payload)
            st.success(f'Creative 建立成功：{creative_id}')
        except Exception as exc:
            st.error(str(exc))


def main() -> None:
    st.title('創意組合工具 (Creative Composer)')

    config = load_config()
    account_options: List[str] = config.get('ad_account_ids') or []
    if not account_options:
        st.warning('config.yaml 尚未設定任何 ad_account_ids。')
        return

    default_account = st.session_state.get('composer_account_id')
    if default_account in account_options:
        default_index = account_options.index(default_account)
    else:
        default_index = 0

    account_id = st.selectbox('選擇 Ad Account', account_options, index=default_index)
    st.session_state.composer_account_id = account_id

    page_id = st.text_input('Facebook Page ID', key='composer_page_id')

    format_choice = st.selectbox('選擇創意格式', CREATIVE_FORMAT_OPTIONS, key='creative_format_choice')

    api = get_api_client()
    account = AdAccount(account_id, api=api)

    if format_choice == '單一圖片/影片':
        render_single_form(account, page_id)
    elif format_choice == '輪播 (Carousel)':
        render_carousel_section(account, page_id)
    elif format_choice == '精品欄 (Collection)':
        render_collection_section(account, page_id)
    else:
        render_raw_section(account)


if __name__ == '__main__':
    main()




