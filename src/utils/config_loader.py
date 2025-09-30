from pathlib import Path
from typing import Any, Dict, Optional

import yaml


DEFAULT_CONFIG_PATH = Path('configs') / 'config.yaml'


def load_config(config_path: Optional[str] = None) -> Dict[str, Any]:
    """Load YAML configuration and return it as a dictionary."""
    if config_path:
        path = Path(config_path)
    else:
        project_root = Path(__file__).resolve().parents[2]
        path = project_root / DEFAULT_CONFIG_PATH

    if not path.exists():
        raise FileNotFoundError(f'Config file not found: {path}')

    with path.open('r', encoding='utf-8-sig') as handle:
        data = yaml.safe_load(handle) or {}

    if not isinstance(data, dict):
        raise ValueError(f'Expected dict config, got {type(data)}')

    return data
