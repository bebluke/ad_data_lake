import json
import random
import sys
import time
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Callable, Dict, List

CURRENT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = CURRENT_DIR.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))
from facebook_business.adobjects.adaccount import AdAccount
from facebook_business.api import FacebookRequest

from src.configs.fields_schema import (
    ADSET_GET_FIELDS,
    AD_GET_FIELDS,
    CAMPAIGN_GET_FIELDS,
    CREATIVE_GET_FIELDS,
    INSIGHT_ACTION_TYPE_GET_FIELDS,
    INSIGHT_ADSET_SUMMARY_FIELDS,
    INSIGHT_AD_SUMMARY_FIELDS,
    INSIGHT_CAMPAIGN_SUMMARY_FIELDS,
    INSIGHT_DEMOGRAPHIC_GET_FIELDS,
)
from src.utils.api_helpers import RATE_LIMIT_ERROR_CODES, make_api_request
from src.utils.config_loader import load_config
from src.utils.storage import save_to_json
from src.utils.client import get_api_client
from src.extractors.api_extractor import (
    fetch_creatives_by_ids,
    fetch_insights,
    objects_to_dict_list,
)

# NOTE: Legacy inline save_to_json helper was commented out in favour of src.utils.storage.save_to_json to avoid duplicated logic.
# def save_to_json(data, folder_path, filename):
#     """Persist structured data to a JSON file."""
#     ...

def get_updated_since_filter():
    """Limit daily mode queries by updated_time filtering."""
    yesterday_timestamp = int((datetime.now() - timedelta(days=1)).timestamp())
    return [
        {
            'field': 'updated_time',
            'operator': 'GREATER_THAN',
            'value': yesterday_timestamp,
        }
    ]

def chunk_list(items: List[Dict], chunk_size: int) -> List[List[Dict]]:
    return [items[i : i + chunk_size] for i in range(0, len(items), chunk_size)]

def create_graph_params(fields: List[str], limit: int = 500, filtering=None) -> Dict:
    params = {
        'fields': ','.join(fields),
        'limit': limit,
    }
    if filtering:
        params['filtering'] = json.dumps(filtering)
    return params

def collect_cursor_data(cursor, page_pause: float = 0.0) -> List[dict]:
    if not cursor:
        return []
    data = objects_to_dict_list(cursor)
    while True:
        if page_pause:
            time.sleep(page_pause)
        has_next = make_api_request(cursor.load_next_page)
        if not has_next:
            break
        data.extend(objects_to_dict_list(cursor))
    return data


def fetch_account_ad_sets(
    account: AdAccount,
    fields: List[str],
    filtering=None,
    page_pause: float = 0.5,
) -> List[dict]:
    params = {'limit': 200}
    if filtering:
        params['filtering'] = filtering
    cursor = make_api_request(lambda: account.get_ad_sets(fields=fields, params=params))
    return collect_cursor_data(cursor, page_pause=page_pause)


def fetch_ad_sets_by_campaigns(
    api,
    campaigns: List[Dict],
    adset_fields: List[str],
    filtering=None,
) -> List[dict]:
    if not campaigns:
        return []
    adset_requests: List[Dict] = []
    for campaign in campaigns:
        campaign_id = campaign.get('id')
        if not campaign_id:
            continue
        params = create_graph_params(adset_fields, limit=200, filtering=filtering)
        request_obj = FacebookRequest(
            node_id=campaign_id,
            method='GET',
            endpoint='/adsets',
            api=api,
        )
        request_obj.add_params(params)
        adset_requests.append(
            {
                'request': request_obj,
                'context': {'campaign_id': campaign_id, 'description': f'{campaign_id}/adsets'},
                'description': f'{campaign_id}/adsets',
            }
        )
    return execute_batch_requests(
        api,
        adset_requests,
        handle_adset_batch_response,
        pause_seconds=5.0,
        max_chunk_retries=4,
        initial_backoff=2.0,
        chunk_size=15,
    )


def fetch_account_ads(
    account: AdAccount,
    fields: List[str],
    filtering=None,
    page_pause: float = 0.5,
) -> List[dict]:
    params = {'limit': 200}
    if filtering:
        params['filtering'] = filtering
    cursor = make_api_request(lambda: account.get_ads(fields=fields, params=params))
    return collect_cursor_data(cursor, page_pause=page_pause)


def fetch_ads_by_adsets(
    api,
    ad_set_ids: List[str],
    ad_fields: List[str],
    filtering=None,
) -> List[dict]:
    if not ad_set_ids:
        return []
    ad_requests: List[Dict] = []
    for ad_set_id in ad_set_ids:
        params = create_graph_params(ad_fields, limit=200, filtering=filtering)
        request_obj = FacebookRequest(
            node_id=ad_set_id,
            method='GET',
            endpoint='/ads',
            api=api,
        )
        request_obj.add_params(params)
        ad_requests.append(
            {
                'request': request_obj,
                'context': {'ad_set_id': ad_set_id, 'description': f'{ad_set_id}/ads'},
                'description': f'{ad_set_id}/ads',
            }
        )
    return execute_batch_requests(
        api,
        ad_requests,
        handle_ads_batch_response,
        pause_seconds=5.0,
        max_chunk_retries=4,
        initial_backoff=2.0,
        chunk_size=15,
    )

def execute_batch_requests(
    api,
    requests: List[Dict],
    response_handler: Callable,
    pause_seconds: float = 3.0,
    max_chunk_retries: int = 5,
    initial_backoff: float = 1.0,
    chunk_size: int = 20,
) -> List[dict]:
    aggregated: List[dict] = []
    if not requests:
        return aggregated
    for chunk in chunk_list(requests, max(1, chunk_size)):
        remaining_entries = list(chunk)
        chunk_results: List[dict] = []
        max_attempts = max(1, max_chunk_retries)
        attempt = 1
        while remaining_entries and attempt <= max_attempts:
            attempt_results: List[dict] = []
            retry_entries: List[Dict] = []
            batch = api.new_batch()
            for entry in remaining_entries:
                request_obj: FacebookRequest = entry['request']
                context = entry.get('context', {})
                description = entry.get('description', '')

                def success_callback(response, request_context=context, collector=attempt_results):
                    try:
                        response_handler(response, request_context, collector)
                    except Exception as exc:
                        label = request_context.get('description') or description or 'unknown'
                        print(f"Error processing batch response for {label}: {exc}")

                def failure_callback(
                    response,
                    request_context=context,
                    desc=description,
                    entry_ref=entry,
                    retry_collector=retry_entries,
                ):
                    try:
                        error_payload = response.json()
                    except Exception:
                        error_payload = str(response)
                    label = request_context.get('description') or desc or 'unknown'
                    should_retry = False
                    error_code = None
                    error_subcode = None
                    combined_message = ''
                    if isinstance(error_payload, dict):
                        error_info = error_payload.get('error') or error_payload
                        code_value = error_info.get('code')
                        subcode_value = error_info.get('error_subcode')
                        try:
                            error_code = int(code_value) if code_value is not None else None
                        except (TypeError, ValueError):
                            error_code = None
                        try:
                            error_subcode = int(subcode_value) if subcode_value is not None else None
                        except (TypeError, ValueError):
                            error_subcode = None
                        messages = [
                            error_info.get('message'),
                            error_info.get('error_user_title'),
                            error_info.get('error_user_msg'),
                        ]
                        combined_message = ' '.join(
                            str(message) for message in messages if isinstance(message, str)
                        )
                        if (
                            error_code in RATE_LIMIT_ERROR_CODES
                            or error_subcode in RATE_LIMIT_ERROR_CODES
                        ):
                            should_retry = True
                        elif combined_message:
                            lowered = combined_message.lower()
                            if (
                                'limit' in lowered
                                or 'too many' in lowered
                            ):
                                should_retry = True
                    print(f"Batch request failed for {label}: {error_payload}")
                    if should_retry:
                        retry_collector.append(entry_ref)
                        print(
                            f'Scheduled retry for {label} due to rate limiting (code={error_code}, subcode={error_subcode}).'
                        )
                batch.add_request(
                    request=request_obj,
                    success=success_callback,
                    failure=failure_callback,
                )
            execution_result = make_api_request(batch.execute)
            if execution_result is None and not attempt_results:
                print('Batch execution returned no result after retries.')
            chunk_results.extend(attempt_results)
            if not retry_entries:
                remaining_entries = []
                break
            if attempt >= max_attempts:
                remaining_entries = retry_entries
                break
            backoff_time = initial_backoff * (2 ** (attempt - 1))
            jitter = random.uniform(0, 0.5)
            sleep_time = backoff_time + jitter
            print(
                f'Retrying batch for {len(retry_entries)} requests after {sleep_time:.2f}s due to rate limiting...'
            )
            time.sleep(sleep_time)
            remaining_entries = retry_entries
            attempt += 1
        aggregated.extend(chunk_results)
        if remaining_entries:
            print(f'Falling back to sequential execution for {len(remaining_entries)} requests...')
            fallback_results: List[dict] = []
            failed_fallback: List[Dict] = []
            for entry in remaining_entries:
                request_obj: FacebookRequest = entry['request']
                context = entry.get('context', {})
                description = entry.get('description', '')
                label = context.get('description') or description or 'unknown'
                response = make_api_request(request_obj.execute, max_retries=3, initial_backoff=5.0)
                if response is None:
                    failed_fallback.append(entry)
                    print(f"Sequential execution failed for {label}.")
                    continue
                try:
                    response_handler(response, context, fallback_results)
                except Exception as exc:
                    failed_fallback.append(entry)
                    print(f"Error processing sequential response for {label}: {exc}")
            aggregated.extend(fallback_results)
            remaining_entries = failed_fallback
            if remaining_entries:
                failed_labels = [entry.get('description', 'unknown') for entry in remaining_entries]
                labels_preview = ', '.join(failed_labels[:5])
                print(
                    f'Unable to recover {len(remaining_entries)} batch requests after retries: {labels_preview}'
                )
        if pause_seconds:
            time.sleep(pause_seconds)
    return aggregated

def handle_adset_batch_response(response, context: Dict, collector: List[dict]):
    payload = response.json()
    data = payload.get('data', []) if isinstance(payload, dict) else []
    collector.extend(data)

def handle_ads_batch_response(response, context: Dict, collector: List[dict]):
    payload = response.json()
    data = payload.get('data', []) if isinstance(payload, dict) else []
    ad_set_id = context.get('ad_set_id')
    for item in data:
        if ad_set_id:
            item['ad_set_id'] = ad_set_id
        collector.append(item)

def main():
    """Main execution entry point."""
    print("Initializing API client...")
    api = get_api_client()
    print("Loading configurations...")
    config = load_config()
    mode = config.get('mode', 'daily')
    ad_account_ids = config['ad_account_ids']
    output_root = Path('output')
    print(f"Running in {mode} mode.")
    campaign_fields = list(CAMPAIGN_GET_FIELDS.keys())
    adset_fields = list(ADSET_GET_FIELDS.keys())
    ad_fields = list(AD_GET_FIELDS.keys())
    # To re-enable demographic breakdown exports, import INSIGHT_DEMOGRAPHIC_GET_FIELDS
    # and restore the derived list below.
    # insight_demographic_fields = list(INSIGHT_DEMOGRAPHIC_GET_FIELDS.keys())
    insight_action_type_fields = list(INSIGHT_ACTION_TYPE_GET_FIELDS.keys())
    insight_ad_summary_fields = list(INSIGHT_AD_SUMMARY_FIELDS.keys())
    insight_adset_summary_fields = list(INSIGHT_ADSET_SUMMARY_FIELDS.keys())
    insight_campaign_summary_fields = list(INSIGHT_CAMPAIGN_SUMMARY_FIELDS.keys())
    # demographic_breakdowns reserved for future insights breakdown exports.
    action_breakdowns = ['action_type']
    filtering = get_updated_since_filter() if mode == 'daily' else None
    for account_id in ad_account_ids:
        print(f"\n{'=' * 20}\nProcessing account: {account_id}\n{'=' * 20}")
        normalized_account_id = account_id if account_id.startswith('act_') else f"act_{account_id}"
        account = AdAccount(normalized_account_id, api=api)
        daily_output_dir = output_root / date.today().strftime('%Y-%m-%d')
        campaign_params = {'limit': 100}
        if filtering:
            campaign_params['filtering'] = filtering
        print("\nFetching Campaigns...")
        campaigns_cursor = make_api_request(lambda: account.get_campaigns(fields=campaign_fields, params=campaign_params))
        campaigns_data = collect_cursor_data(campaigns_cursor)
        if campaigns_data:
            save_to_json(campaigns_data, daily_output_dir, f"campaigns_{normalized_account_id}.json")
        print("\nFetching Ad Sets via account endpoint...")
        ad_sets_data = fetch_account_ad_sets(account, adset_fields, filtering, page_pause=0.5)
        if not ad_sets_data:
            print("Account-level ad set fetch returned no data; falling back to campaign batches.")
            ad_sets_data = fetch_ad_sets_by_campaigns(api, campaigns_data, adset_fields, filtering)
        if ad_sets_data:
            save_to_json(ad_sets_data, daily_output_dir, f"ad_sets_{normalized_account_id}.json")
        print("\nFetching Ads via account endpoint...")
        ads_final_data = fetch_account_ads(account, ad_fields, filtering, page_pause=0.5)
        if not ads_final_data and ad_sets_data:
            print("Account-level ad fetch returned no data; falling back to ad set batches.")
            ad_set_ids = [ad_set.get("id") for ad_set in ad_sets_data if ad_set.get("id")]
            ads_final_data = fetch_ads_by_adsets(api, ad_set_ids, ad_fields, filtering)

        print("\nFlattening creative_id from ad payloads...")
        all_creative_ids = set()
        for ad in ads_final_data:
            creative_payload = ad.get("creative")
            if isinstance(creative_payload, dict):
                creative_id = creative_payload.get("id")
                if creative_id:
                    ad["creative_id"] = creative_id
                    all_creative_ids.add(creative_id)
            ad.pop("creative", None)

        if ads_final_data:
            save_to_json(ads_final_data, daily_output_dir, f"ads_{normalized_account_id}.json")

        # --- Stage 2: Fetch Creative Details in Batches ---
        if all_creative_ids:
            print(f"\nFound {len(all_creative_ids)} unique creatives to fetch details for...")

            creative_fields = list(CREATIVE_GET_FIELDS.keys())
            creative_requests = []
            for creative_id in list(all_creative_ids):
                params = create_graph_params(creative_fields, limit=1)
                request_obj = FacebookRequest(
                    node_id=creative_id,
                    method='GET',
                    endpoint='',
                    api=api,
                )
                request_obj.add_params(params)
                creative_requests.append({
                    'request': request_obj,
                    'context': {'creative_id': creative_id},
                    'description': f'creative:{creative_id}',
                })

            def handle_creative_batch_response(response, context: Dict, collector: List[dict]):
                payload = response.json()
                if isinstance(payload, dict) and payload.get('id'):
                    collector.append(payload)

            creatives_data = execute_batch_requests(
                api,
                creative_requests,
                handle_creative_batch_response,
                pause_seconds=2.0,
                chunk_size=50,
            )

            if creatives_data:
                print(f"Successfully fetched details for {len(creatives_data)} creatives.")
                save_to_json(creatives_data, daily_output_dir, f"creatives_{normalized_account_id}.json")

        if mode == 'backfill':
            print("\nFetching Insights data by date...")
            start_date = date.fromisoformat(config['date_range']['start_date'])
            end_date = date.fromisoformat(config['date_range']['end_date'])
            current_date = start_date
            insights_root = output_root / 'insights'
            while current_date <= end_date:
                date_str = current_date.strftime('%Y-%m-%d')
                date_range = {'since': date_str, 'until': date_str}
                print(f"--- Fetching insights for {date_str} ---")
                insights_folder = insights_root / date_str

                # Short pause to avoid overwhelming the Insights API when iterating per-day.
                time.sleep(2)

                insights_action_type = fetch_insights(
                    account=account,
                    date_range=date_range,
                    level='ad',
                    fields=insight_action_type_fields,
                    breakdowns=None,
                    action_breakdowns=action_breakdowns,
                )
                if insights_action_type:
                    save_to_json(
                        insights_action_type,
                        insights_folder,
                        f"insights_action_type_{normalized_account_id}.json",
                    )

                insights_ad_summary = fetch_insights(
                    account=account,
                    date_range=date_range,
                    level='ad',
                    fields=insight_ad_summary_fields,
                )
                if insights_ad_summary:
                    save_to_json(
                        insights_ad_summary,
                        insights_folder,
                        f"insights_ad_summary_{normalized_account_id}.json",
                    )

                insights_adset_summary = fetch_insights(
                    account=account,
                    date_range=date_range,
                    level='adset',
                    fields=insight_adset_summary_fields,
                )
                if insights_adset_summary:
                    save_to_json(
                        insights_adset_summary,
                        insights_folder,
                        f"insights_adset_summary_{normalized_account_id}.json",
                    )

                insights_campaign_summary = fetch_insights(
                    account=account,
                    date_range=date_range,
                    level='campaign',
                    fields=insight_campaign_summary_fields,
                )
                if insights_campaign_summary:
                    save_to_json(
                        insights_campaign_summary,
                        insights_folder,
                        f"insights_campaign_summary_{normalized_account_id}.json",
                    )

                time.sleep(10)
                current_date += timedelta(days=1)

            print("Finished insights backfill for account.")

        print(f"Finished processing account {normalized_account_id}.")

    print("\nData extraction process finished.")

if __name__ == "__main__":
    main()

