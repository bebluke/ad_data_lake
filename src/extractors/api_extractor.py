import time
from typing import Callable, Dict, Iterable, List, Optional, Tuple

from facebook_business.adobjects.adaccount import AdAccount
from facebook_business.adobjects.adreportrun import AdReportRun
from facebook_business.adobjects.adcreative import AdCreative
from facebook_business.api import FacebookAdsApi
from facebook_business.exceptions import FacebookRequestError

from src.utils.api_helpers import make_api_request

def objects_to_dict_list(api_objects: Iterable) -> List[dict]:
    """Safely convert API SDK objects into plain dictionaries without exhausting cursors."""
    results: List[dict] = []
    if not api_objects:
        return results

    try:
        if isinstance(api_objects, dict):
            iterable = api_objects.values()
        elif isinstance(api_objects, list):
            iterable = api_objects
        elif hasattr(api_objects, "_queue"):
            queue = getattr(api_objects, "_queue", [])
            iterable = list(queue)
            if isinstance(queue, list):
                queue.clear()
        else:
            iterable = api_objects

        for item in iterable:
            if hasattr(item, "export_all_data"):
                results.append(item.export_all_data())
            elif isinstance(item, dict):
                results.append(item)
    except FacebookRequestError as error:
        try:
            code_val = error.api_error_code() if hasattr(error, "api_error_code") else None
        except Exception:
            code_val = None
        try:
            subcode_val = error.api_error_subcode() if hasattr(error, "api_error_subcode") else None
        except Exception:
            subcode_val = None
        message = error.api_error_message() if hasattr(error, "api_error_message") else str(error)
        print(f"Cursor iteration stopped due to API error (code={code_val}, subcode={subcode_val}): {message}")
    except Exception as exc:
        print(f"Unexpected error while iterating API objects: {exc}")

    return results

# NOTE: Legacy fetch_data helper retained for reference but currently unused.
# def fetch_data(
#     api_call_function: Callable[..., Iterable],
#     fields: List[str],
#     filtering: Optional[List[dict]] = None,
#     limit: int = 100,
# ) -> List[dict]:
#     """Fetch data with paging support (deprecated in favour of explicit helpers)."""
#     params = {'limit': limit}
#     if filtering:
#         params['filtering'] = filtering
#
#     cursor = make_api_request(lambda: api_call_function(fields=fields, params=params))
#     if not cursor:
#         return []
#
#     data: List[dict] = []
#     data.extend(objects_to_dict_list(cursor))
#
#     while True:
#         has_next = make_api_request(cursor.load_next_page)
#         if not has_next:
#             break
#         data.extend(objects_to_dict_list(cursor))
#
#     return data

def fetch_insights(
    account: AdAccount,
    date_range: dict,
    level: str,
    fields: List[str],
    breakdowns: Optional[List[str]] = None,
    action_breakdowns: Optional[List[str]] = None,  # Optional action breakdown keys from Meta
    limit: int = 1000,
    max_wait_seconds: int = 600,
) -> List[dict]:
    """Fetch insights via asynchronous jobs with optional breakdown dimensions."""
    params: Dict[str, object] = {
        'level': level,
        'time_range': date_range,
        'fields': fields,
        'limit': limit,
    }

    # Include breakdowns only when provided; they increase job complexity.
    if breakdowns:
        params['breakdowns'] = breakdowns

    if action_breakdowns:
        params['action_breakdowns'] = action_breakdowns

    print(f"DEBUG: Insights Params Sent to API: {params}")

    # Guard against failed async jobs or timeouts.
    async_job = make_api_request(lambda: account.get_insights(params=params, is_async=True))
    if not async_job:
        return []

    # Poll for async job completion with capped wait times.
    job_id = async_job.get_id() if hasattr(async_job, 'get_id') else 'unknown'
    start_time = time.monotonic()

    while True:
        status_job = make_api_request(lambda: async_job.api_get())
        if not status_job:
            print(f"Failed to poll status for job {job_id}")
            return []

        status = status_job.get(AdReportRun.Field.async_status)
        percent = status_job.get(AdReportRun.Field.async_percent_completion)
        print(f"Async insights job {job_id} progress: {percent}% (status={status}).")

        if status == 'Job Completed':
            break
        if status in ['Job Failed', 'Job Skipped', 'Job Stopped', 'Job Cancelled']:
            print(f"Async insights job {job_id} ended with status {status} for params {params}")
            return []
        if time.monotonic() - start_time >= max_wait_seconds:
            print(f"Async insights job {job_id} exceeded max wait of {max_wait_seconds}s.")
            return []

        time.sleep(min(30, 2 ** (time.monotonic() - start_time) // 10))

    insights_cursor = make_api_request(async_job.get_result)
    return objects_to_dict_list(insights_cursor) if insights_cursor else []

def fetch_creatives_by_ids(ids: List[str], fields: List[str], api: FacebookAdsApi, chunk_size: int = 15, chunk_pause: float = 2.5) -> Tuple[List[dict], List[str]]:
    """Fetch creative metadata sequentially with resumable tracking."""
    if not ids:
        return [], []

    results: Dict[str, dict] = {}
    missing: List[str] = []
    step = max(1, chunk_size)

    for offset in range(0, len(ids), step):
        chunk = [str(cid) for cid in ids[offset : offset + step] if cid]
        if not chunk:
            continue
        for creative_id in chunk:
            creative_obj = make_api_request(
                lambda cid=creative_id: AdCreative(cid, api=api).api_get(fields=fields),
                max_retries=5,
                initial_backoff=2.0,
            )
            if not creative_obj:
                missing.append(str(creative_id))
                continue
            if hasattr(creative_obj, 'export_all_data'):
                payload = creative_obj.export_all_data()
            elif isinstance(creative_obj, dict):
                payload = creative_obj
            else:
                payload = None
            if isinstance(payload, dict):
                cid_val = payload.get('id') or str(creative_id)
                results[str(cid_val)] = payload
        if chunk_pause and offset + step < len(ids):
            time.sleep(chunk_pause)

    return list(results.values()), missing

