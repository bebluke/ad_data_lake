import json
import random
import time
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional

try:
    import streamlit as st
except ModuleNotFoundError:  # Streamlit may be unavailable in non-UI contexts
    st = None

from facebook_business.exceptions import FacebookRequestError

RATE_LIMIT_ERROR_CODES = {4, 17, 32, 613, 80004}

ACCOUNT_RATE_LIMIT_SUBCODES = {2446079, 2446094, 2446095}
ACCOUNT_RATE_LIMIT_BACKOFF_SECONDS = 600.0


def _extract_error_subcode(error: FacebookRequestError) -> Optional[int]:
    """Safely extract the API error subcode from FacebookRequestError."""
    for attr in ("api_error_subcode", "error_subcode"):
        value = getattr(error, attr, None)
        if callable(value):
            try:
                return int(value())
            except Exception:
                continue
        if value is not None:
            try:
                return int(value)
            except Exception:
                continue
    return None



def _extract_error_code(error: FacebookRequestError) -> Optional[int]:
    """Safely extract the API error code from FacebookRequestError."""
    for attr in ("api_error_code", "code"):
        value = getattr(error, attr, None)
        if callable(value):
            try:
                return int(value())
            except Exception:
                continue
        if value is not None:
            try:
                return int(value)
            except Exception:
                continue
    return None



def _parse_datetime_value(value: Any) -> Optional[datetime]:
    """Parse various datetime representations into a timezone-aware datetime."""
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    if isinstance(value, (int, float)):
        try:
            return datetime.fromtimestamp(float(value), tz=timezone.utc)
        except (ValueError, OSError, OverflowError):
            return None
    if isinstance(value, str):
        stripped = value.strip()
        if not stripped:
            return None
        normalized = stripped
        if stripped.endswith('Z'):
            normalized = f"{stripped[:-1]}+00:00"
        elif len(stripped) >= 5 and stripped[-5] in ('+', '-') and stripped[-3] != ':':
            normalized = f"{stripped[:-2]}:{stripped[-2:]}"
        try:
            parsed = datetime.fromisoformat(normalized)
        except ValueError:
            for fmt in ('%Y-%m-%d %H:%M:%S', '%Y-%m-%dT%H:%M:%S'):
                try:
                    parsed = datetime.strptime(stripped, fmt)
                except ValueError:
                    continue
                else:
                    return parsed.replace(tzinfo=timezone.utc)
            return None
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed
    return None



def _isoformat_datetime(dt: datetime) -> str:
    """Return an ISO 8601 string without microseconds for a datetime."""
    tz_aware = dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    return tz_aware.replace(microsecond=0).isoformat()



def sanitize_payload(data: Any, object_type: str) -> Any:
    """Recursively clean coercible values and normalize payload before creation API calls."""
    object_type = (object_type or '').lower()
    now_utc = datetime.now(timezone.utc)

    def _parse_positive_amount(value: Any) -> Optional[int]:
        if isinstance(value, str):
            trimmed = value.strip()
            if not trimmed:
                return None
            try:
                value = float(trimmed)
            except ValueError:
                return None
        try:
            amount = float(value)
        except (TypeError, ValueError):
            return None
        if amount <= 0:
            return None
        return int(round(amount))

    def _normalize_string_collection(value: Any) -> Optional[List[str]]:
        if value in (None, '', [], (), set()):
            return []
        if isinstance(value, str):
            trimmed = value.strip()
            if not trimmed or trimmed == '[]':
                return []
            try:
                parsed = json.loads(trimmed)
            except (TypeError, ValueError, json.JSONDecodeError):
                return [item.strip() for item in trimmed.split(',') if item.strip()]
            else:
                if isinstance(parsed, list):
                    return [str(item).strip() for item in parsed if str(item).strip()]
                if parsed is None:
                    return []
                parsed_str = str(parsed).strip()
                return [parsed_str] if parsed_str else []
        if isinstance(value, (list, tuple, set)):
            normalized: List[str] = []
            for item in value:
                if item in (None, '', []):
                    continue
                if isinstance(item, str):
                    item_trimmed = item.strip()
                    if item_trimmed:
                        normalized.append(item_trimmed)
                else:
                    normalized.append(str(item))
            return normalized
        return None

    def _apply_dict_rules(values: Dict[str, Any], depth: int) -> Dict[str, Any]:
        if not values:
            return values

        daily_budget_present = 'daily_budget' in values
        lifetime_budget_present = 'lifetime_budget' in values
        if daily_budget_present or lifetime_budget_present:
            daily_amount = _parse_positive_amount(values.get('daily_budget'))
            lifetime_amount = _parse_positive_amount(values.get('lifetime_budget'))
            if daily_amount is not None:
                values['daily_budget'] = daily_amount
                values.pop('lifetime_budget', None)
            elif lifetime_amount is not None:
                values['lifetime_budget'] = lifetime_amount
                values.pop('daily_budget', None)
            else:
                if daily_budget_present:
                    values.pop('daily_budget', None)
                if lifetime_budget_present:
                    values.pop('lifetime_budget', None)

        if 'spend_cap' in values:
            spend_cap_value = values.get('spend_cap')
            if isinstance(spend_cap_value, str):
                spend_cap_value = spend_cap_value.strip()
            if spend_cap_value in (None, '', '0', 0):
                values.pop('spend_cap', None)
            else:
                normalized_cap = _parse_positive_amount(spend_cap_value)
                if normalized_cap is not None:
                    values['spend_cap'] = normalized_cap
                else:
                    values.pop('spend_cap', None)

        if depth == 0 or 'special_ad_categories' in values:
            normalized_categories = _normalize_string_collection(values.get('special_ad_categories'))
            if normalized_categories is None:
                if depth == 0:
                    values['special_ad_categories'] = []
                else:
                    values.pop('special_ad_categories', None)
            else:
                values['special_ad_categories'] = normalized_categories

        brand_safety_fields = (
            'brand_safety_content_filter_levels',
            'brand_safety_content_severity_levels',
            'excluded_brand_safety_content_types',
        )
        for field_name in brand_safety_fields:
            if field_name not in values:
                continue
            normalized_levels = _normalize_string_collection(values.get(field_name))
            if normalized_levels is None:
                values.pop(field_name, None)
            else:
                values[field_name] = normalized_levels
        start_time_value = values.get('start_time')
        if start_time_value in (None, ''):
            values.pop('start_time', None)
        elif start_time_value is not None:
            parsed_start = _parse_datetime_value(start_time_value)
            if parsed_start:
                values['start_time'] = _isoformat_datetime(parsed_start if parsed_start >= now_utc else now_utc)
            else:
                values.pop('start_time', None)

        time_fields_map = {
            'campaign': ('stop_time',),
            'adset': ('end_time', 'stop_time')
        }
        time_fields = time_fields_map.get(object_type, ('stop_time', 'end_time'))
        for time_field in time_fields:
            if time_field not in values:
                continue
            value = values[time_field]
            if value in (None, ''):
                values.pop(time_field, None)
                continue
            parsed_time = _parse_datetime_value(value)
            if parsed_time:
                values[time_field] = _isoformat_datetime(parsed_time)
            else:
                values.pop(time_field, None)

        numeric_exclusions = {
            'id',
            'account_id',
            'campaign_id',
            'adset_id',
            'creative_id',
            'parent_id',
            'existing_creative_id',
        }

        for key, value in list(values.items()):
            if not isinstance(value, str):
                continue
            stripped = value.strip()
            if not stripped:
                continue
            if key in numeric_exclusions or key.endswith('_id') or key.endswith('_ids'):
                continue
            if stripped.isdigit():
                values[key] = int(stripped)
                continue
            try:
                numeric_value = float(stripped)
            except ValueError:
                continue
            else:
                values[key] = numeric_value

        return values

    def _sanitize(value: Any, depth: int = 0) -> Any:
        if isinstance(value, dict):
            sanitized_dict = {key: _sanitize(sub_value, depth + 1) for key, sub_value in value.items()}
            return _apply_dict_rules(sanitized_dict, depth)
        if isinstance(value, list):
            return [_sanitize(item, depth + 1) for item in value]
        return value

    if isinstance(data, dict):
        return _sanitize(data, 0)
    if isinstance(data, list):
        return [_sanitize(item, 0) for item in data]
    return data
def _decode_unicode_sequences(value: str) -> str:
    if not isinstance(value, str):
        return value
    if '\\u' not in value and '\\x' not in value:
        return value
    try:
        return value.encode('utf-8').decode('unicode_escape')
    except (UnicodeDecodeError, ValueError):
        return value


def _decode_nested(data: Any) -> Any:
    if isinstance(data, dict):
        return {key: _decode_nested(val) for key, val in data.items()}
    if isinstance(data, list):
        return [_decode_nested(item) for item in data]
    if isinstance(data, str):
        return _decode_unicode_sequences(data)
    return data


def _get_error_message(error: FacebookRequestError) -> str:
    for attr in ("api_error_message", "api_error_summary"):
        value = getattr(error, attr, None)
        if callable(value):
            try:
                result = value()
            except Exception:
                continue
        else:
            result = value
        if result:
            return _decode_unicode_sequences(str(result))
    return _decode_unicode_sequences(str(error))



def _extract_error_details(error: FacebookRequestError) -> Optional[Any]:
    body_accessor = getattr(error, "body", None)
    raw: Any = None
    if callable(body_accessor):
        try:
            raw = body_accessor()
        except Exception:
            raw = None
    elif body_accessor is not None:
        raw = body_accessor

    if raw is None:
        return None

    if isinstance(raw, (bytes, bytearray)):
        raw = raw.decode("utf-8", errors="ignore")

    if isinstance(raw, str):
        raw = raw.strip()
        if not raw:
            return None
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            return _decode_unicode_sequences(raw)
        else:
            return _decode_nested(parsed)

    if isinstance(raw, dict):
        return _decode_nested(raw)

    return raw


def _serialize_for_logging(data: Dict[str, Any]) -> Any:
    try:
        return json.loads(json.dumps(data, ensure_ascii=False, default=str))
    except (TypeError, ValueError):
        return data


def _log_payload(data: Dict[str, Any]) -> None:
    serialized = _serialize_for_logging(data)
    if st:
        try:
            st.json(serialized)
        except TypeError:
            st.write(serialized)
    else:
        try:
            print(json.dumps(serialized, ensure_ascii=False))
        except (TypeError, ValueError):
            print(str(serialized))


def _log_info(message: str) -> None:
    if st:
        st.info(message)
    else:
        print(message)


def _log_success(message: str) -> None:
    if st:
        st.success(message)
    else:
        print(message)


def _log_error(message: str) -> None:
    if st:
        st.error(message)
    else:
        print(message)



def create_ad_object(
    creation_function: Callable[[], object],
    payload: Dict[str, Any],
    object_type_for_log: str,
):
    payload = payload or {}
    object_label = object_type_for_log or 'Ad Object'
    object_type = object_label.split(':', 1)[0].strip().lower()

    clean_payload = sanitize_payload(payload, object_type)
    if isinstance(payload, dict):
        payload.clear()
        payload.update(clean_payload)

    _log_info(f'Creating {object_label}')
    _log_payload(clean_payload)

    try:
        result = creation_function()
    except FacebookRequestError as error:
        error_message = _get_error_message(error)
        _log_error(f'{object_label} creation failed: {error_message}')
        error_details = _extract_error_details(error)

        if st:
            st.write('Meta API error details:')
            if isinstance(error_details, dict):
                st.json(error_details)
            elif error_details:
                st.write(error_details)
            else:
                st.write(error_message)
            st.write('Payload sent to Meta:')
        else:
            print('Meta API error details:')
            if isinstance(error_details, dict):
                print(json.dumps(error_details, ensure_ascii=False, indent=2))
            elif error_details:
                print(error_details)
            else:
                print(error_message)
            print('Payload sent to Meta:')

        _log_payload(clean_payload)
        return None

    if result is None:
        _log_error(f'{object_label} returned no response from Meta')
        if st:
            st.write('Payload sent to Meta:')
        else:
            print('Payload sent to Meta:')
        _log_payload(clean_payload)
        return None

    _log_success(f'{object_label} created successfully')
    return result



def make_api_request(
    api_call_function: Callable[[], object],
    max_retries: int = 5,
    initial_backoff: float = 1.0,
):
    """Execute API call with exponential backoff and jitter on rate-limit errors."""
    attempt = 0
    while attempt < max_retries:
        try:
            return api_call_function()
        except FacebookRequestError as error:
            error_code = _extract_error_code(error)
            error_subcode = _extract_error_subcode(error)
            is_rate_limited = (error_code in RATE_LIMIT_ERROR_CODES) or (error_subcode in ACCOUNT_RATE_LIMIT_SUBCODES)
            should_retry = is_rate_limited and attempt < max_retries - 1

            print(
                f"API request error (code={error_code}, subcode={error_subcode}, attempt={attempt + 1}/{max_retries}): {error.api_error_message()}"
            )

            if not should_retry:
                print("Max retries reached or non-retriable error encountered.")
                return None

            backoff_time = initial_backoff * (2 ** attempt)
            if error_subcode in ACCOUNT_RATE_LIMIT_SUBCODES:
                backoff_time = max(backoff_time, ACCOUNT_RATE_LIMIT_BACKOFF_SECONDS)
            jitter = random.uniform(0, 0.5)
            sleep_time = backoff_time + jitter
            print(f"Retrying after {sleep_time:.2f}s due to rate limiting...")
            time.sleep(sleep_time)
            attempt += 1
        except Exception as exc:  # Defensive: unexpected exceptions should not retry endlessly
            print(f"Unexpected error during API request: {exc}")
            return None

    return None





