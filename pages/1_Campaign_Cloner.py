from __future__ import annotations

import copy
import json
import mimetypes
import sys
import tempfile
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

import streamlit as st
from facebook_business.adobjects.ad import Ad
from facebook_business.adobjects.adaccount import AdAccount
from facebook_business.adobjects.adcreative import AdCreative
from facebook_business.adobjects.adimage import AdImage
from facebook_business.adobjects.adset import AdSet
from facebook_business.adobjects.advideo import AdVideo
from facebook_business.adobjects.campaign import Campaign

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.extractors.api_extractor import objects_to_dict_list
from src.tools.campaign_inspector import (
    AD_FIELDS,
    ADSET_FIELDS,
    CAMPAIGN_FIELDS,
    CREATIVE_FIELDS,
    fetch_ad_sets,
    fetch_ads,
    fetch_creatives,
)
from src.configs.fields_schema import (
    AD_POST_FIELDS,
    ADSET_POST_FIELDS,
    CAMPAIGN_POST_FIELDS,
    CREATIVE_POST_FIELDS,
)
from src.utils.api_helpers import create_ad_object, make_api_request, sanitize_payload
from src.utils.client import get_api_client
from src.utils.config_loader import load_config
from src.utils.ui_clipboard import ensure_asset_clipboard, render_asset_clipboard


ensure_asset_clipboard()
render_asset_clipboard()

SESSION_DEFAULTS: Dict[str, Any] = {
    'campaigns_list': [],
    'selected_campaign_id': None,
    'template_data': None,
    'uploaded_files': [],
    'new_asset_map': {},
    'selected_account_id': None,
    'campaigns_loaded_for': None,
}

VIDEO_MIME_PREFIX = 'video/'
VIDEO_EXTENSIONS = {'.mp4', '.mov', '.avi', '.mkv', '.wmv'}
NO_ASSET_PLACEHOLDER = '[No Asset]'



BUDGET_FIELDS = {'daily_budget', 'lifetime_budget', 'spend_cap', 'bid_amount'}
BOOLEAN_FIELDS = {'is_dynamic_creative'}
JSON_FIELD_HINTS = {'promoted_object', 'targeting', 'attribution_spec', 'special_ad_categories'}
TRUTHY_VALUES = {'true', '1', 'yes', 'y'}
FALSY_VALUES = {'false', '0', 'no', 'n'}


def get_field_label(field_name: str, schema_dict: Dict[str, Dict[str, str]]) -> str:
    meta = schema_dict.get(field_name, {})
    zh_name = meta.get('zh_tw', field_name)
    return f"{zh_name} ({field_name})"


def format_default_value(value: Any) -> str:
    if value is None:
        return ''
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False)
    return str(value)


def parse_budget_default(value: Any) -> float:
    try:
        if value in (None, ''):
            return 0.0
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def normalize_input_value(field_name: str, value: Any, original_value: Any) -> Any:
        if value is None:
            return None
    
        if field_name in BUDGET_FIELDS:
            if value == '':
                return None
            if isinstance(value, (int, float)):
                return str(int(round(value)))
            if isinstance(value, str):
                trimmed = value.strip()
                if not trimmed:
                    return None
                try:
                    return str(int(round(float(trimmed))))
                except ValueError:
                    return trimmed
            try:
                return str(int(round(float(value))))
            except (TypeError, ValueError):
                return str(value)
    
        if field_name in BOOLEAN_FIELDS or isinstance(original_value, bool):
            if isinstance(value, bool):
                return value
            if isinstance(value, str):
                lowered = value.strip().lower()
                if lowered in TRUTHY_VALUES:
                    return True
                if lowered in FALSY_VALUES:
                    return False
            return bool(value)
    
        if isinstance(value, (dict, list)):
            return value
    
        if isinstance(original_value, (dict, list)):
            if isinstance(value, str):
                try:
                    return json.loads(value)
                except json.JSONDecodeError:
                    return original_value
            return value
    
        if field_name in JSON_FIELD_HINTS:
            if isinstance(value, str):
                trimmed = value.strip()
                if not trimmed:
                    return [] if field_name == 'special_ad_categories' else None
                try:
                    return json.loads(trimmed)
                except json.JSONDecodeError:
                    if field_name == 'special_ad_categories':
                        return [item.strip() for item in trimmed.split(',') if item.strip()]
            return value
    
        if isinstance(original_value, (int, float)):
            if isinstance(value, (int, float)):
                return type(original_value)(value)
            if isinstance(value, str):
                trimmed = value.strip()
                if not trimmed:
                    return None
                try:
                    numeric = float(trimmed)
                    return type(original_value)(numeric)
                except (TypeError, ValueError):
                    pass
    
        if isinstance(value, str):
            trimmed = value.strip()
            if trimmed.startswith('{') or trimmed.startswith('['):
                try:
                    return json.loads(trimmed)
                except json.JSONDecodeError:
                    pass
            if trimmed.lower() in TRUTHY_VALUES:
                return True
            if trimmed.lower() in FALSY_VALUES:
                return False
            return trimmed
    
        return value
    
    

def render_field_widget(field_name: str, schema_dict: Dict[str, Dict[str, str]], default_value: Any, key_prefix: str) -> Any:
    label = get_field_label(field_name, schema_dict)
    widget_key = f"{key_prefix}_{field_name}"
    if field_name in BUDGET_FIELDS:
        return st.number_input(
            label=label,
            min_value=0.0,
            value=parse_budget_default(default_value),
            step=1.0,
            key=widget_key,
        )
    if field_name in BOOLEAN_FIELDS or isinstance(default_value, bool):
        if isinstance(default_value, str):
            lowered = default_value.strip().lower()
            if lowered in TRUTHY_VALUES:
                default_bool = True
            elif lowered in FALSY_VALUES:
                default_bool = False
            else:
                default_bool = bool(default_value)
        else:
            default_bool = bool(default_value)
        return st.checkbox(
            label=label,
            value=default_bool,
            key=widget_key,
        )
    if field_name in JSON_FIELD_HINTS or isinstance(default_value, (dict, list)):
        return st.text_area(
            label=label,
            value=format_default_value(default_value),
            key=widget_key,
            height=160,
        )
    return st.text_input(
        label=label,
        value=format_default_value(default_value),
        key=widget_key,
    )


def render_object_fields(
    schema_dict: Dict[str, Dict[str, str]],
    defaults: Optional[Dict[str, Any]],
    key_prefix: str,
    skip_fields: Optional[Iterable[str]] = None,
) -> Dict[str, Any]:
    collected: Dict[str, Any] = {}
    skip = set(skip_fields or ())
    defaults = defaults or {}
    for field_name in schema_dict.keys():
        if field_name in skip:
            continue
        default_value = defaults.get(field_name)
        collected[field_name] = render_field_widget(field_name, schema_dict, default_value, key_prefix)
    return collected

def is_campaign_budget_optimized(campaign: Dict[str, Any]) -> bool:
        for field in ('daily_budget', 'lifetime_budget'):
            value = campaign.get(field)
            try:
                if value is not None and float(value) > 0:
                    return True
            except (TypeError, ValueError):
                if value not in (None, '', '0'):
                    return True
        return False
    
    
def parse_creative_spec(spec: Optional[Dict[str, Any]]) -> Dict[str, str]:
    parsed = {'message': '', 'title': '', 'link': ''}
    if not isinstance(spec, dict):
        return parsed

    template_data = spec.get('template_data')
    if isinstance(template_data, dict):
        message = template_data.get('message')
        if isinstance(message, str):
            parsed['message'] = message
        link_candidate = template_data.get('link') or template_data.get('link_url')
        if isinstance(link_candidate, str):
            parsed['link'] = link_candidate
        call_to_action = template_data.get('call_to_action')
        if isinstance(call_to_action, dict):
            value = call_to_action.get('value')
            if isinstance(value, dict):
                link_value = value.get('link') or value.get('link_url')
                if isinstance(link_value, str) and not parsed['link']:
                    parsed['link'] = link_value
        child_attachments = template_data.get('child_attachments')
        if isinstance(child_attachments, list) and child_attachments:
            first_child = child_attachments[0]
            if isinstance(first_child, dict):
                title_candidate = first_child.get('name') or first_child.get('title')
                if isinstance(title_candidate, str):
                    parsed['title'] = title_candidate

    link_data = spec.get('link_data')
    if isinstance(link_data, dict):
        if not parsed['message']:
            message = link_data.get('message')
            if isinstance(message, str):
                parsed['message'] = message
        if not parsed['title']:
            title_candidate = link_data.get('headline') or link_data.get('name')
            if isinstance(title_candidate, str):
                parsed['title'] = title_candidate
        if not parsed['link']:
            link_candidate = link_data.get('link') or link_data.get('link_url')
            if isinstance(link_candidate, str):
                parsed['link'] = link_candidate
        call_to_action = link_data.get('call_to_action')
        if isinstance(call_to_action, dict):
            value = call_to_action.get('value')
            if isinstance(value, dict) and not parsed['link']:
                link_value = value.get('link') or value.get('link_url')
                if isinstance(link_value, str):
                    parsed['link'] = link_value

    video_data = spec.get('video_data')
    if isinstance(video_data, dict):
        if not parsed['message']:
            message = video_data.get('message')
            if isinstance(message, str):
                parsed['message'] = message
        if not parsed['title']:
            title_candidate = video_data.get('title')
            if isinstance(title_candidate, str):
                parsed['title'] = title_candidate
        call_to_action = video_data.get('call_to_action')
        if isinstance(call_to_action, dict):
            value = call_to_action.get('value')
            if isinstance(value, dict) and not parsed['link']:
                link_value = value.get('link') or value.get('link_url')
                if isinstance(link_value, str):
                    parsed['link'] = link_value

    photo_data = spec.get('photo_data')
    if isinstance(photo_data, dict) and not parsed['message']:
        message = photo_data.get('message')
        if isinstance(message, str):
            parsed['message'] = message

    for key, value in parsed.items():
        if value is None:
            parsed[key] = ''
        elif not isinstance(value, str):
            parsed[key] = str(value)

    return parsed


def extract_creative_edit_defaults(creative: Optional[Dict[str, Any]]) -> Dict[str, str]:
    defaults = {'message': '', 'title': '', 'link': ''}
    if not isinstance(creative, dict):
        return defaults

    spec_defaults = parse_creative_spec(creative.get('object_story_spec'))

    message = spec_defaults['message'] or creative.get('body') or ''
    title = spec_defaults['title'] or creative.get('title') or ''
    link = spec_defaults['link'] or ''

    if not link:
        link = creative.get('object_url') or creative.get('link_url') or link
    if not link:
        call_to_action = creative.get('call_to_action')
        if isinstance(call_to_action, dict):
            value = call_to_action.get('value')
            if isinstance(value, dict):
                link_candidate = value.get('link') or value.get('link_url')
                if isinstance(link_candidate, str):
                    link = link_candidate

    defaults['message'] = message if isinstance(message, str) else str(message)
    defaults['title'] = title if isinstance(title, str) else str(title)
    defaults['link'] = link if isinstance(link, str) else (str(link) if link else '')
    return defaults



def extract_retailer_item_ids(creative: Optional[Dict[str, Any]]) -> List[str]:
    items: List[str] = []
    if not isinstance(creative, dict):
        return items
    story_spec = creative.get('object_story_spec')
    if isinstance(story_spec, dict):
        direct_ids = story_spec.get('retailer_item_ids')
        if isinstance(direct_ids, list):
            items.extend(str(item) for item in direct_ids if str(item).strip())
        for section_key in ('link_data', 'video_data', 'template_data'):
            section = story_spec.get(section_key)
            if isinstance(section, dict):
                section_ids = section.get('retailer_item_ids')
                if isinstance(section_ids, list):
                    items.extend(str(item) for item in section_ids if str(item).strip())
    seen = set()
    ordered: List[str] = []
    for item in items:
        normalized = item.strip()
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        ordered.append(normalized)
    return ordered


def ensure_session_state() -> None:
        for key, default in SESSION_DEFAULTS.items():
            if key not in st.session_state:
                if isinstance(default, (dict, list)):
                    st.session_state[key] = copy.deepcopy(default)
                else:
                    st.session_state[key] = default
    
    
def infer_asset_kind(uploaded_file) -> str:
        mime = getattr(uploaded_file, 'type', '') or mimetypes.guess_type(uploaded_file.name)[0] or ''
        suffix = Path(uploaded_file.name).suffix.lower()
        if mime.startswith(VIDEO_MIME_PREFIX) or suffix in VIDEO_EXTENSIONS:
            return 'video'
        return 'image'
    
    
def load_campaigns_for_account(api, account_id: str) -> List[Dict[str, Any]]:
        account = AdAccount(account_id, api=api)
        params = {'limit': 200}
        cursor = make_api_request(lambda: account.get_campaigns(fields=[Campaign.Field.id, Campaign.Field.name], params=params))
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
    
    
def extract_default_text(creative: Optional[Dict[str, Any]]) -> Tuple[str, str]:
        if not isinstance(creative, dict):
            return '', ''
        story_spec = creative.get('object_story_spec')
        message = ''
        headline = ''
        if isinstance(story_spec, dict):
            link_data = story_spec.get('link_data')
            video_data = story_spec.get('video_data')
            photo_data = story_spec.get('photo_data')
            if isinstance(link_data, dict):
                message = link_data.get('message', '')
                headline = link_data.get('headline') or link_data.get('name') or ''
            elif isinstance(video_data, dict):
                message = video_data.get('message', '')
                headline = video_data.get('title', '')
            elif isinstance(photo_data, dict):
                message = photo_data.get('message', '')
        message = message or creative.get('body', '') or ''
        headline = headline or creative.get('title', '') or ''
        return message, headline
    
    
def fetch_campaign_details(api, campaign_id: str) -> Dict[str, Any]:
        campaign_obj = make_api_request(lambda: Campaign(campaign_id, api=api).api_get(fields=CAMPAIGN_FIELDS))
        if hasattr(campaign_obj, 'export_all_data'):
            campaign_data = campaign_obj.export_all_data()
        elif isinstance(campaign_obj, dict):
            campaign_data = campaign_obj
        else:
            campaign_data = {}
    
        ad_sets = fetch_ad_sets(api, campaign_id)
        ad_set_ids = [str(item.get('id')) for item in ad_sets if isinstance(item, dict) and item.get('id')]
    
        ads = fetch_ads(api, ad_set_ids) if ad_set_ids else []
        creative_ids: List[str] = []
        for ad in ads:
            creative_info = ad.get('creative') if isinstance(ad, dict) else None
            creative_id = creative_info.get('id') if isinstance(creative_info, dict) else None
            if creative_id:
                creative_ids.append(str(creative_id))
        unique_creative_ids = sorted(set(creative_ids))
    
        creatives = fetch_creatives(api, unique_creative_ids) if unique_creative_ids else []
        creative_index = {str(item.get('id')): item for item in creatives if isinstance(item, dict)}
    
        structured_ad_sets: List[Dict[str, Any]] = []
        for ad_set in ad_sets:
            adset_id = ad_set.get('id') if isinstance(ad_set, dict) else None
            cloned_set = copy.deepcopy(ad_set) if isinstance(ad_set, dict) else {}
            cloned_set['ads'] = []
            for ad in ads:
                if str(ad.get('adset_id')) != str(adset_id):
                    continue
                entry = copy.deepcopy(ad)
                creative_info = entry.get('creative') if isinstance(entry, dict) else None
                creative_id = creative_info.get('id') if isinstance(creative_info, dict) else None
                creative_details = creative_index.get(str(creative_id)) if creative_id else None
                entry['creative_details'] = creative_details
                default_message, default_headline = extract_default_text(creative_details)
                entry['default_message'] = default_message
                entry['default_headline'] = default_headline
                cloned_set['ads'].append(entry)
            structured_ad_sets.append(cloned_set)
    
        return {
            'campaign': campaign_data,
            'ad_sets': structured_ad_sets,
            'creatives': creatives,
        }
    
    
def upload_asset(api, account_id: str, uploaded_file) -> Dict[str, str]:
        account = AdAccount(account_id, api=api)
        asset_kind = infer_asset_kind(uploaded_file)
        suffix = Path(uploaded_file.name).suffix or ('.mp4' if asset_kind == 'video' else '.jpg')
        with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as temp_file:
            temp_file.write(uploaded_file.getbuffer())
            temp_path = temp_file.name
    
        temp_path_obj = Path(temp_path)
        try:
            if asset_kind == 'video':
                def _upload_video():
                    with open(temp_path_obj, 'rb') as handle:
                        return account.create_ad_video(
                            params={'name': uploaded_file.name},
                            files={'source': handle},
                        )
    
                response = make_api_request(_upload_video)
                if not response:
                    raise RuntimeError('上傳影片素材到 Meta 失敗。')
                video_id = response.get('id') if isinstance(response, dict) else getattr(response, 'get_id', lambda: None)()
                if not video_id:
                    raise RuntimeError('上傳的影片未回傳資產 ID。')
                return {'type': 'video', 'key': 'video_id', 'value': str(video_id)}
    
            def _upload_image():
                return account.create_ad_image(
                    params={'filename': str(temp_path_obj)},
                )
    
            response = make_api_request(_upload_image)
            if not response:
                raise RuntimeError('上傳圖片素材到 Meta 失敗。')
            image_hash = response.get('hash') if isinstance(response, dict) else response.get(AdImage.Field.hash)
            if not image_hash:
                raise RuntimeError('圖片素材未回傳 image_hash。')
            return {'type': 'image', 'key': 'image_hash', 'value': str(image_hash)}
        finally:
            try:
                temp_path_obj.unlink()
            except FileNotFoundError:
                pass
    
    
def resolve_budget_field(ad_set: Dict[str, Any]) -> Tuple[str, float]:
        daily = ad_set.get('daily_budget')
        lifetime = ad_set.get('lifetime_budget')
        if daily:
            return 'daily_budget', float(daily)
        if lifetime:
            return 'lifetime_budget', float(lifetime)
        return 'daily_budget', 0.0
    
    
    
    
def update_object_story_spec(
        story_spec: Optional[Dict[str, Any]],
        asset_info: Optional[Dict[str, str]],
        message: Optional[str],
        headline: Optional[str],
        link: Optional[str],
        retailer_item_ids: Optional[List[str]],
    ) -> Dict[str, Any]:
    updated = copy.deepcopy(story_spec) if isinstance(story_spec, dict) else {}
    if not isinstance(updated, dict):
        updated = {}

    if asset_info:
        key = asset_info.get('key')
        value = asset_info.get('value')
        if key and value:
            link_data = updated.get('link_data')
            if isinstance(link_data, dict):
                link_data = dict(link_data)
                if key == 'image_hash':
                    link_data['image_hash'] = value
                    link_data.pop('video_id', None)
                elif key == 'video_id':
                    link_data['video_id'] = value
                    link_data.pop('image_hash', None)
                updated['link_data'] = link_data
            video_data = updated.get('video_data')
            if isinstance(video_data, dict):
                video_data = dict(video_data)
                if key == 'video_id':
                    video_data['video_id'] = value
                updated['video_data'] = video_data
            photo_data = updated.get('photo_data')
            if isinstance(photo_data, dict):
                photo_data = dict(photo_data)
                if key == 'image_hash':
                    photo_data['image_hash'] = value
                updated['photo_data'] = photo_data

    if message:
        for section in ('link_data', 'video_data', 'photo_data'):
            section_data = updated.get(section)
            if isinstance(section_data, dict):
                cloned = dict(section_data)
                cloned['message'] = message
                updated[section] = cloned

    if headline:
        link_data = updated.get('link_data')
        if isinstance(link_data, dict):
            link_data = dict(link_data)
            link_data['headline'] = headline
            link_data['name'] = headline
            updated['link_data'] = link_data
        video_data = updated.get('video_data')
        if isinstance(video_data, dict):
            video_data = dict(video_data)
            video_data['title'] = headline
            updated['video_data'] = video_data

    if link:
        link_data = updated.get('link_data')
        if isinstance(link_data, dict):
            link_data = dict(link_data)
            link_data['link'] = link
            link_data['link_url'] = link
            call_to_action = link_data.get('call_to_action')
            if isinstance(call_to_action, dict):
                value_dict = call_to_action.get('value')
                if isinstance(value_dict, dict):
                    value_dict = dict(value_dict)
                    value_dict['link'] = link
                    value_dict['link_url'] = link
                    call_to_action['value'] = value_dict
                else:
                    call_to_action['value'] = {'link': link}
                link_data['call_to_action'] = call_to_action
            updated['link_data'] = link_data
        for section in ('video_data', 'photo_data'):
            section_data = updated.get(section)
            if isinstance(section_data, dict):
                cloned = dict(section_data)
                call_to_action = cloned.get('call_to_action')
                if isinstance(call_to_action, dict):
                    value_dict = call_to_action.get('value')
                    if isinstance(value_dict, dict):
                        value_dict = dict(value_dict)
                        value_dict['link'] = link
                        value_dict['link_url'] = link
                        call_to_action['value'] = value_dict
                    else:
                        call_to_action['value'] = {'link': link}
                    cloned['call_to_action'] = call_to_action
                updated[section] = cloned

    cleaned_retailer_ids: List[str] = []
    if retailer_item_ids:
        for item in retailer_item_ids:
            normalized = item.strip() if isinstance(item, str) else str(item).strip()
            if normalized and normalized not in cleaned_retailer_ids:
                cleaned_retailer_ids.append(normalized)
    if cleaned_retailer_ids:
        for section in ('link_data', 'video_data', 'template_data'):
            section_data = updated.get(section)
            if isinstance(section_data, dict):
                section_clone = dict(section_data)
                section_clone['retailer_item_ids'] = cleaned_retailer_ids
                updated[section] = section_clone
        updated['retailer_item_ids'] = cleaned_retailer_ids
    else:
        for section in ('link_data', 'video_data', 'template_data'):
            section_data = updated.get(section)
            if isinstance(section_data, dict) and 'retailer_item_ids' in section_data:
                section_clone = dict(section_data)
                section_clone.pop('retailer_item_ids', None)
                updated[section] = section_clone
        updated.pop('retailer_item_ids', None)

    return updated


def sanitize_campaign_payload(
        template: Dict[str, Any],
        overrides: Dict[str, Any],
        is_cbo: bool,
    ) -> Dict[str, Any]:
    base = template if isinstance(template, dict) else {}
    payload: Dict[str, Any] = {}
    for field in CAMPAIGN_POST_FIELDS.keys():
        if field in {'daily_budget', 'lifetime_budget'} and not is_cbo:
            continue
        original_value = base.get(field)
        value = overrides.get(field, original_value)
        normalized = normalize_input_value(field, value, original_value)
        if normalized is None:
            continue
        payload[field] = normalized
    payload['status'] = payload.get('status') or 'PAUSED'
    return payload


def sanitize_adset_payload(
        template: Dict[str, Any],
        overrides: Dict[str, Any],
        new_campaign_id: str,
        is_cbo: bool,
    ) -> Dict[str, Any]:
    base = template if isinstance(template, dict) else {}
    payload: Dict[str, Any] = {}
    for field in ADSET_POST_FIELDS.keys():
        if field == 'campaign_id':
            continue
        if field in {'daily_budget', 'lifetime_budget'} and is_cbo:
            continue
        original_value = base.get(field)
        value = overrides.get(field, original_value)
        normalized = normalize_input_value(field, value, original_value)
        if normalized is None:
            continue
        payload[field] = normalized
    payload['campaign_id'] = new_campaign_id
    payload['status'] = payload.get('status') or 'PAUSED'
    if is_cbo:
        payload.pop('daily_budget', None)
        payload.pop('lifetime_budget', None)
    return payload


def build_creative_payload(
        creative_template: Optional[Dict[str, Any]],
        creative_inputs: Dict[str, Any],
        asset_map: Dict[str, Dict[str, str]],
    ) -> Tuple[Optional[Dict[str, Any]], Optional[Dict[str, str]]]:
    if not isinstance(creative_template, dict):
        return None, None
    payload = copy.deepcopy(creative_template)
    for field in ['id', 'status', 'thumbnail_url', 'image_url', 'effective_object_story_id', 'asset_feed_spec']:
        payload.pop(field, None)
    asset_name = creative_inputs.get('asset_name')
    asset_info = asset_map.get(asset_name) if asset_name else None
    message = creative_inputs.get('message') or ''
    headline = creative_inputs.get('title') or ''
    link = creative_inputs.get('link') or ''
    retailer_ids_raw = creative_inputs.get('retailer_item_ids') or ''
    retailer_ids_list = [item.strip() for item in retailer_ids_raw.split(',') if item and item.strip()]
    story_spec = payload.get('object_story_spec')
    payload['object_story_spec'] = update_object_story_spec(
        story_spec,
        asset_info,
        message,
        headline,
        link,
        retailer_ids_list,
    )
    if message:
        payload['body'] = message
    else:
        payload.pop('body', None)
    if headline:
        payload['title'] = headline
    else:
        payload.pop('title', None)
    if retailer_ids_list:
        payload['retailer_item_ids'] = retailer_ids_list
    else:
        payload.pop('retailer_item_ids', None)
    creative_name = creative_inputs.get('name')
    if creative_name:
        payload['name'] = creative_name
    return payload, asset_info


def sanitize_ad_payload(
        template: Dict[str, Any],
        overrides: Dict[str, Any],
        new_adset_id: str,
        creative_id: str,
    ) -> Dict[str, Any]:
    base = template if isinstance(template, dict) else {}
    payload: Dict[str, Any] = {}
    for field in AD_POST_FIELDS.keys():
        if field in {'creative', 'adset_id'}:
            continue
        original_value = base.get(field)
        value = overrides.get(field, original_value)
        normalized = normalize_input_value(field, value, original_value)
        if normalized is None:
            continue
        payload[field] = normalized
    payload['adset_id'] = new_adset_id
    payload['creative'] = {'creative_id': creative_id}
    payload['status'] = payload.get('status') or 'PAUSED'
    return payload






def create_campaign_from_template(
        api,
        account_id: str,
        template_data: Dict[str, Any],
        user_inputs: Dict[str, Any],
        asset_map: Dict[str, Dict[str, str]],
        is_cbo: bool,
    ) -> Optional[Dict[str, Any]]:
    account = AdAccount(account_id, api=api)
    campaign_template = template_data.get('campaign', {}) if isinstance(template_data.get('campaign'), dict) else {}
    campaign_overrides = user_inputs.get('campaign', {}) if isinstance(user_inputs.get('campaign'), dict) else {}
    campaign_payload = sanitize_campaign_payload(campaign_template, campaign_overrides, is_cbo)
    campaign_params = sanitize_payload(campaign_payload, 'campaign')

    if is_cbo and not any(key in campaign_params for key in ('daily_budget', 'lifetime_budget')):
        raise ValueError('使用 CBO 的 Campaign 必須設定 daily_budget 或 lifetime_budget 中至少一項')

    campaign_display_name = campaign_params.get('name') or '未命名 Campaign'
    campaign_result = create_ad_object(
        creation_function=lambda: account.create_campaign(params=campaign_params),
        payload=campaign_params,
        object_type_for_log=f"Campaign: {campaign_display_name}",
    )
    if not campaign_result:
        return None

    new_campaign_id = campaign_result.get('id') if isinstance(campaign_result, dict) else getattr(campaign_result, 'get', lambda *_: None)('id')
    if not new_campaign_id:
        st.error('無法取得新的 Campaign ID')
        print('無法取得新的 Campaign ID')
        return None

    st.info(f"Campaign '{campaign_display_name}' 建立成功！ID: {new_campaign_id}")

    created_adsets: List[str] = []
    created_ads: List[str] = []

    adset_inputs_map = user_inputs.get('ad_sets', {}) if isinstance(user_inputs, dict) else {}
    ad_sets_template = template_data.get('ad_sets', []) if isinstance(template_data.get('ad_sets'), list) else []
    total_ad_sets = len(ad_sets_template)

    for index, ad_set in enumerate(ad_sets_template):
        adset_key = str(ad_set.get('id')) if ad_set.get('id') else f'adset_{index}'
        adset_display_name = ad_set.get('name', adset_key)
        st.write(f"正在建立 Ad Set {index + 1}/{max(total_ad_sets, 1)}：'{adset_display_name}'...")

        adset_inputs = adset_inputs_map.get(adset_key, {}) if isinstance(adset_inputs_map, dict) else {}
        adset_field_overrides = adset_inputs.get('fields', {}) if isinstance(adset_inputs, dict) else {}
        adset_payload = sanitize_adset_payload(ad_set, adset_field_overrides, str(new_campaign_id), is_cbo)
        adset_params = sanitize_payload(adset_payload, 'adset')

        if not is_cbo and not any(key in adset_params for key in ('daily_budget', 'lifetime_budget')):
            raise ValueError(f"Ad Set {adset_display_name} 缺少預算設定，請輸入每日或整體預算。")

        adset_result = create_ad_object(
            creation_function=lambda: account.create_ad_set(params=adset_params),
            payload=adset_params,
            object_type_for_log=f"Ad Set: {adset_display_name}",
        )
        if not adset_result:
            return None

        new_adset_id = adset_result.get('id') if isinstance(adset_result, dict) else getattr(adset_result, 'get', lambda *_: None)('id')
        if not new_adset_id:
            st.error('無法取得新的 Ad Set ID')
            print('無法取得新的 Ad Set ID')
            return None

        created_adsets.append(str(new_adset_id))
        st.info(f"Ad Set '{adset_display_name}' 建立成功！ID: {new_adset_id}")

        ad_inputs_map = adset_inputs.get('ads', {}) if isinstance(adset_inputs, dict) else {}
        ad_templates = ad_set.get('ads', []) if isinstance(ad_set.get('ads'), list) else []
        total_ads = len(ad_templates)

        for ad_index, ad in enumerate(ad_templates):
            adset_specific_key = str(ad.get('id')) if ad.get('id') else f'{adset_key}_ad_{ad_index}'
            ad_display_name = ad.get('name', adset_specific_key)
            st.write(f"  處理 Ad {ad_index + 1}/{max(total_ads, 1)}：'{ad_display_name}'")

            ad_input = ad_inputs_map.get(adset_specific_key, {}) if isinstance(ad_inputs_map, dict) else {}
            ad_field_overrides = ad_input.get('fields', {}) if isinstance(ad_input, dict) else {}
            creative_inputs = ad_input.get('creative', {}) if isinstance(ad_input, dict) else {}
            creative_template = ad.get('creative_details')
            creative_payload, _ = build_creative_payload(creative_template, creative_inputs, asset_map)

            if not creative_payload:
                st.warning(f"Ad '{ad_display_name}' �ʤ֥i�Ϊ��зN�]�w�A�w���L�C")
                print(f"Ad '{ad_display_name}' skipped due to missing creative payload.")
                continue

            creative_payload = sanitize_payload(creative_payload, 'creative')

            creative_result = create_ad_object(
                creation_function=lambda: account.create_ad_creative(params=creative_payload),
                payload=creative_payload,
                object_type_for_log=f"Creative（Ad '{ad_display_name}'）",
            )
            if not creative_result:
                return None

            new_creative_id = creative_result.get('id') if isinstance(creative_result, dict) else getattr(creative_result, 'get', lambda *_: None)('id')
            if not new_creative_id:
                st.error('無法取得新的 Creative ID')
                print('無法取得新的 Creative ID')
                return None

            st.write(f"    Creative 建立成功，ID: {new_creative_id}")

            ad_payload = sanitize_ad_payload(ad, ad_field_overrides, str(new_adset_id), str(new_creative_id))
            ad_payload = sanitize_payload(ad_payload, 'ad')
            ad_result = create_ad_object(
                creation_function=lambda: account.create_ad(params=ad_payload),
                payload=ad_payload,
                object_type_for_log=f"Ad: {ad_display_name}",
            )
            if not ad_result:
                return None

            new_ad_id = ad_result.get('id') if isinstance(ad_result, dict) else getattr(ad_result, 'get', lambda *_: None)('id')
            if not new_ad_id:
                st.error('無法取得新的 Ad ID')
                print('無法取得新的 Ad ID')
                return None

            created_ads.append(str(new_ad_id))
            st.info(f"Ad '{ad_display_name}' 建立成功！ID: {new_ad_id}")

    return {
        'campaign_id': str(new_campaign_id),
        'adset_ids': created_adsets,
        'ad_ids': created_ads,
    }

def reset_flow() -> None:
    for key, default in SESSION_DEFAULTS.items():
        if isinstance(default, list):
            st.session_state[key] = []
        elif isinstance(default, dict):
            st.session_state[key] = {}
        else:
            st.session_state[key] = None


def render_selector(api, account_options: List[str]) -> Optional[str]:
    st.title('Campaign 複製工具')
    if not account_options:
        st.warning('config.yaml 未設定任何 ad_account_ids。')
        return None

    default_account = st.session_state.get('selected_account_id')
    try:
        index = account_options.index(default_account) if default_account in account_options else 0
    except ValueError:
        index = 0

    account_id = st.selectbox('請選擇廣告帳戶', account_options, index=index if account_options else 0)
    st.session_state.selected_account_id = account_id

    if account_id and st.session_state.get('campaigns_loaded_for') != account_id:
        with st.spinner('正在載入 Campaign 清單...'):
            st.session_state.campaigns_list = load_campaigns_for_account(api, account_id)
            st.session_state.campaigns_loaded_for = account_id

    campaigns = st.session_state.get('campaigns_list', [])
    if not campaigns:
        st.info('目前沒有可供複製的 Campaign。')
        return None

    option_labels = [
        f"{item.get('name', '未命名 Campaign')} ({item.get('id')})"
        for item in campaigns
    ]
    selected_label = st.selectbox('請選擇要複製的 Campaign', ['請選擇 Campaign'] + option_labels)
    if selected_label and selected_label != '請選擇 Campaign':
        idx = option_labels.index(selected_label)
        selected_campaign = campaigns[idx]
        st.session_state.selected_campaign_id = selected_campaign.get('id')
        st.rerun()
    return None


def render_clone_form(api, account_id: str) -> None:
    template = st.session_state.get('template_data')
    if template is None:
        with st.spinner('正在載入 Campaign 詳細資訊...'):
            template = fetch_campaign_details(api, st.session_state.selected_campaign_id)
            st.session_state.template_data = template

    if not isinstance(template, dict):
        st.error('無法取得 Campaign 詳細資料。')
        return

    campaign_info = template.get('campaign', {}) if isinstance(template.get('campaign'), dict) else {}
    is_cbo = is_campaign_budget_optimized(campaign_info)

    user_inputs: Dict[str, Any] = {'campaign': {}, 'ad_sets': {}}
    ad_sets = template.get('ad_sets', []) if isinstance(template.get('ad_sets'), list) else []

    with st.sidebar:
        st.header('上傳素材檔案')
        uploaded_files = st.file_uploader('上傳素材檔案 (圖片/影片)', accept_multiple_files=True, key='asset_uploader')
        if uploaded_files is not None:
            st.session_state.uploaded_files = list(uploaded_files)
            st.subheader('已上傳的素材')
        preview_files = st.session_state.get('uploaded_files', [])
        if preview_files:
            st.subheader('已上傳素材預覽')
            for uploaded_file in preview_files:
                try:
                    uploaded_file.seek(0)
                except Exception:
                    pass
                file_type = getattr(uploaded_file, 'type', '') or ''
                caption_text = uploaded_file.name
                if file_type.startswith('image/'):
                    st.image(uploaded_file, width=150)
                elif file_type.startswith('video/'):
                    st.video(uploaded_file)
                else:
                    caption_text = f"{uploaded_file.name} （無法預覽此格式）"
                st.caption(caption_text)

    uploaded_file_names = [item.name for item in st.session_state.get('uploaded_files', [])]
    asset_options = [NO_ASSET_PLACEHOLDER] + uploaded_file_names if uploaded_file_names else [NO_ASSET_PLACEHOLDER]

    with st.form(key='clone_form'):
        campaign_skip_fields = {'daily_budget', 'lifetime_budget'} if not is_cbo else set()
        campaign_inputs = render_object_fields(
            schema_dict=CAMPAIGN_POST_FIELDS,
            defaults=campaign_info,
            key_prefix='campaign',
            skip_fields=campaign_skip_fields,
        )
        user_inputs['campaign'] = campaign_inputs
        for index, ad_set in enumerate(ad_sets):
            adset_key = str(ad_set.get('id')) if ad_set.get('id') else f'adset_{index}'
            adset_entry: Dict[str, Any] = {'fields': {}, 'ads': {}}
            user_inputs['ad_sets'][adset_key] = adset_entry

            adset_name = ad_set.get('name', f'Ad Set {adset_key}')
            adset_identifier = str(ad_set.get('id')) if ad_set.get('id') else adset_key
            header_columns = st.columns([0.85, 0.15])
            with header_columns[1]:
                if st.form_submit_button('刪除 Ad Set', key=f'delete_adset_{adset_identifier}'):
                    template_state = st.session_state.get('template_data')
                    if isinstance(template_state, dict) and isinstance(template_state.get('ad_sets'), list):
                        remaining_ad_sets = []
                        for candidate in template_state['ad_sets']:
                            candidate_id = candidate.get('id')
                            if ad_set.get('id'):
                                if str(candidate_id) == str(ad_set.get('id')):
                                    continue
                            elif candidate is ad_set:
                                continue
                            remaining_ad_sets.append(candidate)
                        template_state['ad_sets'] = remaining_ad_sets
                    st.rerun()
            with header_columns[0]:
                with st.expander(adset_name, expanded=False):
                    adset_skip_fields = {'campaign_id'}
                    if is_cbo:
                        adset_skip_fields.update({'daily_budget', 'lifetime_budget'})
                    adset_defaults = ad_set if isinstance(ad_set, dict) else {}
                    adset_fields = render_object_fields(
                        schema_dict=ADSET_POST_FIELDS,
                        defaults=adset_defaults,
                        key_prefix=f'adset_{adset_key}',
                        skip_fields=adset_skip_fields,
                    )
                    adset_entry['fields'] = adset_fields
                    ads = ad_set.get('ads', []) if isinstance(ad_set.get('ads'), list) else []
                    for ad_index, ad in enumerate(ads):
                        ad_key = str(ad.get('id')) if ad.get('id') else f'{adset_key}_ad_{ad_index}'
                        ad_entry: Dict[str, Any] = {'fields': {}, 'creative': {}}
                        adset_entry['ads'][ad_key] = ad_entry

                        st.markdown('---')
                        ad_identifier = str(ad.get('id')) if ad.get('id') else ad_key
                        ad_columns = st.columns([0.8, 0.2])
                        with ad_columns[0]:
                            st.markdown(f"**{ad.get('name', f'Ad {ad_key}')}**")
                        with ad_columns[1]:
                            if st.form_submit_button('刪除 Ad', key=f'delete_ad_{adset_identifier}_{ad_identifier}'):
                                template_state = st.session_state.get('template_data')
                                if isinstance(template_state, dict):
                                    for candidate_set in template_state.get('ad_sets', []):
                                        candidate_id = candidate_set.get('id')
                                        same_set = False
                                        if ad_set.get('id'):
                                            same_set = str(candidate_id) == str(ad_set.get('id'))
                                        else:
                                            same_set = candidate_set is ad_set
                                        if same_set and isinstance(candidate_set.get('ads'), list):
                                            remaining_ads = []
                                            for candidate_ad in candidate_set['ads']:
                                                candidate_ad_id = candidate_ad.get('id')
                                                if ad.get('id'):
                                                    if str(candidate_ad_id) == str(ad.get('id')):
                                                        continue
                                                elif candidate_ad is ad:
                                                    continue
                                                remaining_ads.append(candidate_ad)
                                            candidate_set['ads'] = remaining_ads
                                            break
                                st.rerun()

                        selected_asset = st.selectbox(
                            '請選擇素材資產',
                            asset_options,
                            key=f'asset_{ad_key}',
                        )
                        ad_entry['creative']['asset_name'] = None if selected_asset == NO_ASSET_PLACEHOLDER else selected_asset
                        ad_defaults = ad if isinstance(ad, dict) else {}
                        ad_fields = render_object_fields(
                            schema_dict=AD_POST_FIELDS,
                            defaults=ad_defaults,
                            key_prefix=f'ad_{ad_key}',
                            skip_fields={'creative', 'adset_id'},
                        )
                        ad_entry['fields'] = ad_fields
                        creative_details = ad.get('creative_details')
                        creative_defaults = extract_creative_edit_defaults(creative_details)
                        object_story_spec = None
                        if isinstance(creative_details, dict):
                            object_story_spec = creative_details.get('object_story_spec')
                        spec_defaults = parse_creative_spec(object_story_spec)
                        message_default = spec_defaults['message'] or creative_defaults['message'] or ad.get('default_message', '')
                        title_default = spec_defaults['title'] or creative_defaults['title'] or ad.get('default_headline', '')
                        link_default = spec_defaults['link'] or creative_defaults['link'] or ad.get('default_link', '')
                        message_value = st.text_area(
                            label=get_field_label('message', CREATIVE_POST_FIELDS),
                            value=message_default,
                            key=f'ad_{ad_key}_creative_message',
                        )
                        title_value = st.text_area(
                            label=get_field_label('title', CREATIVE_POST_FIELDS),
                            value=title_default,
                            key=f'ad_{ad_key}_creative_title',
                        )
                        link_value = st.text_input(
                            label='目的地網址 (link)',
                            value=link_default,
                            key=f'ad_{ad_key}_creative_link',
                        )

                        existing_retailer_ids = extract_retailer_item_ids(creative_details)
                        retailer_ids_default = ', '.join(existing_retailer_ids)
                        retailer_ids_value = st.text_area(
                            label='商品 ID (retailer_item_ids)',
                            value=retailer_ids_default,
                            key=f'ad_{ad_key}_creative_retailer_ids',
                            help='請以逗號分隔多個商品 ID',
                        )
                        ad_entry['creative'].update(
                            {
                                'message': message_value.strip(),
                                'title': title_value.strip(),
                                'link': link_value.strip(),
                                'retailer_item_ids': retailer_ids_value.strip(),
                            }
                        )
        submit_button = st.form_submit_button('送出並建立 Campaign')

    

    if submit_button:
        try:
            with st.spinner('正在上傳素材到 Meta...'):
                asset_map: Dict[str, Dict[str, str]] = {}
                for uploaded in st.session_state.get('uploaded_files', []) or []:
                    asset_info = upload_asset(api, account_id, uploaded)
                    asset_map[uploaded.name] = asset_info
                st.session_state.new_asset_map = asset_map

            with st.spinner('正在建立 Campaign 結構...'):
                result = create_campaign_from_template(
                    api=api,
                    account_id=account_id,
                    template_data=template,
                    user_inputs=user_inputs,
                    asset_map=st.session_state.get('new_asset_map', {}),
                    is_cbo=is_cbo,
                )

            if result is None:
                st.info('建置流程已中止，請檢查上方錯誤訊息。')
                st.button('重新設定', on_click=reset_flow)
            else:
                st.success(f"Campaign 建置完成！ID: {result.get('campaign_id')}")
                st.button('再建立一個 Campaign', on_click=reset_flow)
        except Exception as exc:
            st.error(f'建立 Campaign 發生錯誤：{exc}')
            st.button('重新設定', on_click=reset_flow)
    else:
        st.button('返回 Campaign 選擇', on_click=reset_flow)

def main() -> None:
    ensure_session_state()
    config = load_config()
    account_options = config.get('ad_account_ids', []) if isinstance(config, dict) else []

    try:
        api = get_api_client()
    except Exception as exc:
        st.error(f'取得 API 客戶端失敗：{exc}')
        return

    if not st.session_state.get('selected_campaign_id'):
        render_selector(api, account_options)
        return

    account_id = st.session_state.get('selected_account_id')
    if not account_id:
        st.warning('請選擇廣告帳戶。')
        reset_flow()
        return

    render_clone_form(api, account_id)


if __name__ == '__main__':
    main()




























