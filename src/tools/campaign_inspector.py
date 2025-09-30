import argparse
import json
import os
import sys
import time
from pathlib import Path
from typing import Dict, Iterable, List, Tuple

CURRENT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = CURRENT_DIR.parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from facebook_business.adobjects.adaccount import AdAccount
from facebook_business.adobjects.campaign import Campaign
from facebook_business.api import FacebookRequest

from src.extractors.api_extractor import objects_to_dict_list
from src.extractors.get_pixels import fetch_pixels_for_account
from src.utils.api_helpers import make_api_request
from src.utils.client import get_api_client
from src.utils.storage import save_to_json

CAMPAIGN_FIELDS = [
    'id',
    'name',
    'status',
    'effective_status',
    'objective',
    'buying_type',
    'start_time',
    'stop_time',
    'budget_remaining',
    'daily_budget',
    'lifetime_budget',
    'bid_strategy',
    'created_time',
    'updated_time',
    'promoted_object',
]

ADSET_FIELDS = [
    'id',
    'name',
    'status',
    'effective_status',
    'campaign_id',
    'daily_budget',
    'lifetime_budget',
    'start_time',
    'end_time',
    'promoted_object',
    'optimization_goal',
    'billing_event',
    'bid_amount',
    'targeting',
    'pacing_type',
    'created_time',
    'updated_time',
]

AD_FIELDS = [
    'id',
    'name',
    'status',
    'effective_status',
    'adset_id',
    'campaign_id',
    'creative',
    'conversion_domain',
    'tracking_specs',
    'source_ad_id',
    'created_time',
    'updated_time',
]

CREATIVE_FIELDS = [
    'id',
    'name',
    'status',
    'object_story_spec',
    'asset_feed_spec',
    'body',
    'title',
    'image_url',
    'thumbnail_url',
    'video_id',
    'url_tags',
    'effective_object_story_id',
    'instagram_actor_id',
    'call_to_action_type',
]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description='Inspect a campaign hierarchy and enrich with pixel details.')
    parser.add_argument('--account', required=True, help='Ad account ID (accepts formats with or without act_ prefix).')
    parser.add_argument('--campaign', required=True, help='Campaign ID to inspect.')
    parser.add_argument(
        '--output-dir',
        default=os.path.join('output', 'inspector'),
        help='Destination folder for the generated report.',
    )
    return parser.parse_args()


def chunk_list(items: List, size: int) -> Iterable[List]:
    step = max(1, size)
    for index in range(0, len(items), step):
        yield items[index : index + step]


def execute_batch_requests(
    api,
    requests: List[Tuple[FacebookRequest, Dict]],
    success_handler,
    pause_seconds: float = 1.0,
    chunk_size: int = 20,
) -> List[dict]:
    collected: List[dict] = []
    if not requests:
        return collected

    for chunk in chunk_list(requests, chunk_size):
        batch = api.new_batch()
        for request_obj, context in chunk:
            def _success(response, ctx=context):
                try:
                    success_handler(response, ctx, collected)
                except Exception as exc:
                    label = ctx.get('description') or ctx.get('id') or 'unknown'
                    print(f'Failed to process batch response for {label}: {exc}')

            def _failure(response, ctx=context):
                try:
                    payload = response.json()
                except Exception:
                    payload = str(response)
                label = ctx.get('description') or ctx.get('id') or 'unknown'
                print(f'Batch request failed for {label}: {payload}')

            batch.add_request(request=request_obj, success=_success, failure=_failure)

        make_api_request(batch.execute)
        if pause_seconds:
            time.sleep(pause_seconds)

    return collected


def fetch_ad_sets(api, campaign_id: str) -> List[dict]:
    campaign = Campaign(campaign_id, api=api)
    params = {'limit': 200}
    cursor = make_api_request(lambda: campaign.get_ad_sets(fields=ADSET_FIELDS, params=params))
    if not cursor:
        return []

    ad_sets: List[dict] = []
    while True:
        ad_sets.extend(objects_to_dict_list(cursor))
        has_next = make_api_request(cursor.load_next_page)
        if not has_next:
            break
    return ad_sets


def fetch_ads(api, ad_set_ids: List[str]) -> List[dict]:
    requests: List[Tuple[FacebookRequest, Dict]] = []
    for ad_set_id in ad_set_ids:
        params = {
            'fields': ','.join(AD_FIELDS),
            'limit': 200,
        }
        request = FacebookRequest(node_id=ad_set_id, method='GET', endpoint='/ads', api=api)
        request.add_params(params)
        requests.append((request, {'ad_set_id': ad_set_id, 'description': f'{ad_set_id}/ads'}))

    def handle_ads(response, context: Dict, collector: List[dict]) -> None:
        payload = response.json()
        data = payload.get('data') if isinstance(payload, dict) else None
        if not isinstance(data, list):
            return
        for item in data:
            if isinstance(item, dict):
                item.setdefault('adset_id', context.get('ad_set_id'))
                collector.append(item)

    return execute_batch_requests(api, requests, handle_ads, pause_seconds=1.5, chunk_size=15)


def fetch_creatives(api, creative_ids: List[str]) -> List[dict]:
    requests: List[Tuple[FacebookRequest, Dict]] = []
    for creative_id in creative_ids:
        params = {
            'fields': ','.join(CREATIVE_FIELDS),
            'limit': 1,
        }
        request = FacebookRequest(node_id=creative_id, method='GET', endpoint='', api=api)
        request.add_params(params)
        requests.append((request, {'creative_id': creative_id, 'description': f'creative:{creative_id}'}))

    def handle_creatives(response, context: Dict, collector: List[dict]) -> None:
        payload = response.json()
        if isinstance(payload, dict) and payload.get('id'):
            collector.append(payload)

    return execute_batch_requests(api, requests, handle_creatives, pause_seconds=1.0, chunk_size=25)


def build_pixel_index(pixels: List[dict]) -> Dict[str, dict]:
    indexed: Dict[str, dict] = {}
    for pixel in pixels:
        if not isinstance(pixel, dict):
            continue
        pixel_id = pixel.get('id')
        if pixel_id:
            indexed[str(pixel_id)] = pixel
    return indexed


def enrich_ad_sets(ad_sets: List[dict], ads: List[dict], pixel_index: Dict[str, dict]) -> List[dict]:
    ads_by_adset: Dict[str, List[dict]] = {}
    for ad in ads:
        if not isinstance(ad, dict):
            continue
        adset_id = ad.get('adset_id')
        if not adset_id:
            continue
        ads_by_adset.setdefault(str(adset_id), []).append(ad)

    enriched: List[dict] = []
    for ad_set in ad_sets:
        if not isinstance(ad_set, dict):
            continue
        cloned = dict(ad_set)
        promoted_object = cloned.get('promoted_object')
        pixel_id = None
        if isinstance(promoted_object, dict):
            pixel_id = promoted_object.get('pixel_id')
        if pixel_id:
            pixel_details = pixel_index.get(str(pixel_id))
            if pixel_details:
                cloned['tracking_pixel_details'] = pixel_details
        adset_id = cloned.get('id')
        cloned['ads'] = ads_by_adset.get(str(adset_id), [])
        enriched.append(cloned)
    return enriched


def main() -> None:
    args = parse_args()
    account_id = args.account
    if not account_id.startswith('act_'):
        account_id = f'act_{account_id}'

    api = get_api_client()
    account = AdAccount(account_id, api=api)

    campaign_id = args.campaign
    campaign_obj = make_api_request(lambda: Campaign(campaign_id, api=api).api_get(fields=CAMPAIGN_FIELDS))
    if not campaign_obj:
        raise RuntimeError(f'Unable to retrieve campaign {campaign_id}.')

    if hasattr(campaign_obj, 'export_all_data'):
        campaign_data = campaign_obj.export_all_data()
    elif isinstance(campaign_obj, dict):
        campaign_data = campaign_obj
    else:
        campaign_data = {}

    pixels = fetch_pixels_for_account(account)
    pixel_index = build_pixel_index(pixels)

    ad_sets = fetch_ad_sets(api, campaign_id)
    ad_set_ids = [adset.get('id') for adset in ad_sets if isinstance(adset, dict) and adset.get('id')]

    ads = fetch_ads(api, [str(adset_id) for adset_id in ad_set_ids]) if ad_set_ids else []

    creative_ids: List[str] = []
    for ad in ads:
        creative_info = ad.get('creative') if isinstance(ad, dict) else None
        if isinstance(creative_info, dict):
            creative_id = creative_info.get('id')
            if creative_id:
                creative_ids.append(str(creative_id))
    unique_creative_ids = sorted(set(creative_ids))

    creatives = fetch_creatives(api, unique_creative_ids) if unique_creative_ids else []

    enriched_ad_sets = enrich_ad_sets(ad_sets, ads, pixel_index)

    final_report = {
        'account_id': account_id,
        'campaign': campaign_data,
        'ad_sets': enriched_ad_sets,
        'creatives': creatives,
        'pixel_overview': pixels,
    }

    output_dir = args.output_dir
    os.makedirs(output_dir, exist_ok=True)
    filename = f'campaign_{campaign_id}_report.json'
    save_to_json(final_report, output_dir, filename)
    print(json.dumps({'campaign_id': campaign_id, 'ad_sets': len(enriched_ad_sets), 'ads': len(ads), 'creatives': len(creatives)}, indent=2))


if __name__ == '__main__':
    main()

