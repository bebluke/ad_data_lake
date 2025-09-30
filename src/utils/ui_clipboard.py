from __future__ import annotations

from typing import Dict, List, Optional

import streamlit as st

_LABEL_MAP: Dict[str, str] = {
    'image_hash': 'Image Hash',
    'video_id': 'Video ID',
    'creative_id': 'Creative ID',
    'thumbnail_hash': 'Thumbnail Hash',
    'id': 'Asset ID',
}

_DEFAULT_LABEL = 'Asset ID'


def ensure_asset_clipboard() -> None:
    """Ensure clipboard session state exists and is normalized."""
    clipboard = st.session_state.get('asset_clipboard')
    if clipboard is None:
        st.session_state.asset_clipboard = []
        return

    if not isinstance(clipboard, list):
        st.session_state.asset_clipboard = []
        return

    normalized: List[Dict[str, str]] = []
    for item in clipboard:
        if isinstance(item, dict):
            value = item.get('value') or item.get('identifier') or item.get('id')
            if not value:
                continue
            label = item.get('label') or item.get('type') or _DEFAULT_LABEL
            normalized.append({'label': str(label), 'value': str(value)})
        elif isinstance(item, str) and item:
            normalized.append({'label': _DEFAULT_LABEL, 'value': item})

    st.session_state.asset_clipboard = normalized


def _inject_clipboard_styles() -> None:
    if st.session_state.get('_clipboard_styles_injected'):
        return
    st.session_state['_clipboard_styles_injected'] = True
    st.markdown(
        """
        <style>
        :root {
            --clipboard-sidebar-width: 320px;
        }
        [data-testid="stSidebar"] {
            left: auto;
            right: 0;
            border-left: 1px solid var(--secondary-background-color);
            border-right: none;
            width: var(--clipboard-sidebar-width);
        }
        main[data-testid="stAppViewContainer"] {
            margin-left: 0 !important;
            margin-right: calc(var(--clipboard-sidebar-width) + 1rem) !important;
        }
        @media (max-width: 960px) {
            main[data-testid="stAppViewContainer"] {
                margin-right: 0 !important;
            }
            [data-testid="stSidebar"] {
                width: 100%;
            }
        }
        [data-testid="stSidebar"] button[title="Copy to clipboard"] {
            background-color: #4C8BF5 !important;
            color: #ffffff !important;
            border-color: #3367D6 !important;
            border-radius: 0.4rem !important;
            padding: 0.2rem 0.75rem !important;
        }
        [data-testid="stSidebar"] button[title="Copy to clipboard"]:hover {
            background-color: #3367D6 !important;
        }
        [data-testid="stSidebar"] .clipboard-delete button {
            background-color: transparent !important;
            color: var(--text-color) !important;
            border: 1px solid rgba(255, 255, 255, 0.15) !important;
            border-radius: 0.4rem !important;
        }
        </style>
        """,
        unsafe_allow_html=True,
    )


def _resolve_label(identifier_type: Optional[str], asset_type: Optional[str]) -> str:
    if identifier_type:
        mapped = _LABEL_MAP.get(identifier_type.lower())
        if mapped:
            return mapped
        cleaned = identifier_type.replace('_', ' ').title()
        return cleaned
    if asset_type:
        return f"{asset_type.title()} ID"
    return _DEFAULT_LABEL


def add_asset_to_clipboard(
    *,
    identifier: Optional[str],
    identifier_type: Optional[str] = None,
    asset_type: Optional[str] = None,
    file_name: Optional[str] = None,
) -> None:
    """Append a new clipboard entry with descriptive metadata."""
    if not identifier:
        return

    ensure_asset_clipboard()

    label = _resolve_label(identifier_type, asset_type)
    st.session_state.asset_clipboard.append({'label': label, 'value': str(identifier)})


def render_asset_clipboard(force_refresh: bool = False) -> None:
    """Render the clipboard in the sidebar (styled to appear on the right)."""
    ensure_asset_clipboard()
    _inject_clipboard_styles()

    placeholder = st.session_state.get('_clipboard_placeholder')
    if placeholder is None:
        placeholder = st.sidebar.empty()
        st.session_state['_clipboard_placeholder'] = placeholder
    else:
        placeholder.empty()

    clipboard = st.session_state.asset_clipboard

    with placeholder.container():
        st.subheader('Clipboard')
        if not clipboard:
            st.info('No IDs have been captured yet.')
            return

        for index, entry in enumerate(clipboard):
            label = entry.get('label') or _DEFAULT_LABEL
            value = entry.get('value') or ''
            st.markdown(f"**{label}**")
            st.code(value, language='text')

            delete_key = f"clipboard_delete_{index}_{abs(hash(value)) % 1_000_000}"
            cols = st.columns([1.0, 0.35])
            with cols[1]:
                if st.button('Delete', key=delete_key, use_container_width=True, help='Remove this entry'):
                    del st.session_state.asset_clipboard[index]
                    rerun = getattr(st, 'rerun', None)
                    if callable(rerun):
                        rerun()
                    else:
                        legacy_rerun = getattr(st, 'experimental_rerun', None)
                        if callable(legacy_rerun):
                            legacy_rerun()
                        else:
                            st.warning('Please refresh the page to update the clipboard.')
            st.divider()
