import json
import os
import sys
from datetime import date
from pathlib import Path
from typing import List

from facebook_business.adobjects.adaccount import AdAccount

CURRENT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = CURRENT_DIR.parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.extractors.api_extractor import objects_to_dict_list
from src.utils.api_helpers import make_api_request
from src.utils.client import get_api_client
from src.utils.config_loader import load_config
from src.utils.storage import save_to_json

PIXEL_FIELDS = ['id', 'name', 'last_fired_time']


def fetch_pixels_for_account(account: AdAccount) -> List[dict]:
    """Fetch all pixels for the provided ad account."""
    cursor = make_api_request(lambda: account.get_ad_pixels(fields=PIXEL_FIELDS))
    if not cursor:
        return []

    pixels: List[dict] = []
    while True:
        pixels.extend(objects_to_dict_list(cursor))
        has_next = make_api_request(cursor.load_next_page)
        if not has_next:
            break
    return pixels


if __name__ == '__main__':
    config = load_config()
    account_ids = config.get('ad_account_ids') or []
    if not account_ids:
        print('No ad_account_ids found in config.')
        raise SystemExit(0)

    api = get_api_client()
    today = date.today().isoformat()
    output_dir = os.path.join('output', today)

    for account_id in account_ids:
        normalized_id = account_id if account_id.startswith('act_') else f'act_{account_id}'
        account = AdAccount(normalized_id, api=api)
        pixel_data = fetch_pixels_for_account(account)
        print(json.dumps({'account_id': normalized_id, 'pixel_count': len(pixel_data)}))
        filename = f'pixels_{normalized_id}.json'
        save_to_json(pixel_data, output_dir, filename)

