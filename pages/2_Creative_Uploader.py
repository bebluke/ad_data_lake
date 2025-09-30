from __future__ import annotations

import sys
import tempfile
import time
from pathlib import Path
from typing import Any, Dict, List

import streamlit as st
from facebook_business.adobjects.adaccount import AdAccount
from facebook_business.adobjects.adimage import AdImage
from facebook_business.adobjects.advideo import AdVideo

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.utils.api_helpers import make_api_request
from src.utils.client import get_api_client
from src.utils.config_loader import load_config
from src.utils.ui_clipboard import add_asset_to_clipboard, ensure_asset_clipboard, render_asset_clipboard

ensure_asset_clipboard()
render_asset_clipboard()
IMAGE_EXTENSIONS = {'.jpg', '.jpeg', '.png', '.gif', '.bmp', '.webp'}
VIDEO_EXTENSIONS = {'.mp4', '.mov', '.avi', '.mkv', '.wmv'}
IMAGE_MIME_PREFIX = 'image/'
VIDEO_MIME_PREFIX = 'video/'


def object_to_dict(obj: Any) -> Dict[str, Any]:
    if isinstance(obj, dict):
        return obj
    exporter = getattr(obj, 'export_all_data', None)
    if callable(exporter):
        data = exporter()
        if isinstance(data, dict):
            return data
    return {}


def infer_asset_type(uploaded_file) -> str:
    mime_type = (getattr(uploaded_file, 'type', '') or '').lower()
    if mime_type.startswith(IMAGE_MIME_PREFIX):
        return 'image'
    if mime_type.startswith(VIDEO_MIME_PREFIX):
        return 'video'
    suffix = Path(getattr(uploaded_file, 'name', '')).suffix.lower()
    if suffix in IMAGE_EXTENSIONS:
        return 'image'
    if suffix in VIDEO_EXTENSIONS:
        return 'video'
    raise ValueError(f'Unsupported file type for {uploaded_file.name}')


def wait_for_video_ready(account: AdAccount, video_id: str, timeout: float = 600.0, interval: float = 5.0) -> None:
    video = AdVideo(video_id, api=account.get_api())
    start = time.time()
    while True:
        response = make_api_request(lambda: video.api_get(fields=[AdVideo.Field.status]))
        if not response:
            raise RuntimeError('Unable to fetch video processing status.')
        data = object_to_dict(response)
        status = (data.get('status') or '').lower()
        if status == 'ready':
            return
        if status in {'error', 'processing_error', 'failed'}:
            raise RuntimeError(f'Video processing failed with status: {status}')
        if time.time() - start > timeout:
            raise TimeoutError('Video processing timed out.')
        time.sleep(interval)


def upload_asset(account: AdAccount, uploaded_file) -> Dict[str, str]:
    asset_type = infer_asset_type(uploaded_file)
    suffix = Path(uploaded_file.name).suffix
    if not suffix:
        suffix = '.mp4' if asset_type == 'video' else '.jpg'

    try:
        uploaded_file.seek(0)
    except Exception:
        pass

    with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as temp_file:
        temp_file.write(uploaded_file.getbuffer())
        temp_path = Path(temp_file.name)

    try:
        if asset_type == 'video':
            with temp_path.open('rb') as handle:
                response = make_api_request(
                    lambda: account.create_ad_video(
                        params={'name': uploaded_file.name},
                        files={'source': handle},
                    )
                )
            if not response:
                raise RuntimeError('Video upload failed.')
            data = object_to_dict(response)
            video_id = data.get('id') or data.get('video_id')
            if not video_id:
                getter = getattr(response, 'get_id', None)
                if callable(getter):
                    video_id = getter()
            if not video_id:
                raise RuntimeError('Video upload did not return an ID.')
            wait_for_video_ready(account, str(video_id))
            return {
                'file_name': uploaded_file.name,
                'asset_type': 'video',
                'identifier_type': 'video_id',
                'identifier': str(video_id),
            }

        response = make_api_request(lambda: account.create_ad_image(params={'filename': str(temp_path)}))
        if not response:
            raise RuntimeError('Image upload failed.')
        data = object_to_dict(response)
        image_hash = data.get('hash') or data.get(AdImage.Field.hash)
        if not image_hash:
            getter = getattr(response, 'get', None)
            if callable(getter):
                try:
                    image_hash = getter('hash')
                except Exception:
                    image_hash = None
        if not image_hash:
            raise RuntimeError('Image upload did not return a hash.')
        return {
            'file_name': uploaded_file.name,
            'asset_type': 'image',
            'identifier_type': 'image_hash',
            'identifier': str(image_hash),
        }
    finally:
        try:
            temp_path.unlink()
        except FileNotFoundError:
            pass


def main() -> None:
    st.title('素材上傳工具 (Creative Uploader)')

    config = load_config()
    account_options: List[str] = config.get('ad_account_ids') or []
    if not account_options:
        st.warning('config.yaml 尚未設定任何 ad_account_ids。')
        return

    default_account = st.session_state.get('uploader_account_id')
    if default_account in account_options:
        default_index = account_options.index(default_account)
    else:
        default_index = 0

    account_id = st.selectbox('選擇 Ad Account', account_options, index=default_index)
    st.session_state.uploader_account_id = account_id

    uploaded_files = st.file_uploader(
        '選擇要上傳的素材 (支援圖片與影片，最多可多選)',
        accept_multiple_files=True,
        key='creative_asset_uploader',
    )

    if st.button('開始上傳'):
        if not account_id:
            st.warning('請先選擇 Ad Account。')
            return
        if not uploaded_files:
            st.warning('請先選擇至少一個素材檔案。')
            return

        api = get_api_client()
        account = AdAccount(account_id, api=api)

        results: List[Dict[str, str]] = []
        errors: List[str] = []
        progress = st.progress(0, text='上傳中...')
        total = len(uploaded_files)

        clipboard_updated = False

        for idx, uploaded_file in enumerate(uploaded_files, start=1):
            try:
                result = upload_asset(account, uploaded_file)
                results.append(result)
                identifier = result.get('identifier')
                if identifier:
                    add_asset_to_clipboard(
                        identifier=identifier,
                        identifier_type=result.get('identifier_type'),
                        asset_type=result.get('asset_type'),
                        file_name=result.get('file_name'),
                    )
                    clipboard_updated = True
            except TimeoutError as exc:
                errors.append(f'{uploaded_file.name}: {exc}')
            except Exception as exc:
                errors.append(f'{uploaded_file.name}: {exc}')
            finally:
                progress.progress(idx / total, text=f'Processed {idx}/{total} files')

        if clipboard_updated:
            render_asset_clipboard()

        progress.empty()

        if results:
            st.success(f'成功上傳 {len(results)} 個素材。')
            table_rows = [
                {
                    'file_name': item['file_name'],
                    'asset_type': item['asset_type'],
                    'identifier_type': item['identifier_type'],
                    'identifier': item['identifier'],
                }
                for item in results
            ]
            st.dataframe(table_rows, use_container_width=True)

        if errors:
            st.error('部分素材上傳失敗，詳情如下：')
            for message in errors:
                st.error(message)


if __name__ == '__main__':
    main()








