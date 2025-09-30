import json
import os
from pathlib import Path
from typing import Any, Union

PathLike = Union[str, os.PathLike]


def save_to_json(data: Any, folder_path: PathLike, filename: str) -> None:
    """Persist JSON-serialisable data under the specified folder."""
    path = Path(folder_path)
    path.mkdir(parents=True, exist_ok=True)
    file_path = path / filename
    with file_path.open('w', encoding='utf-8') as handle:
        json.dump(data, handle, ensure_ascii=False, indent=4)
    print(f"Successfully saved data to {file_path}")
