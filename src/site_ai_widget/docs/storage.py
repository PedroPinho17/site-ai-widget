"""On-disk storage for uploaded PDFs (uuid filenames only)."""
from __future__ import annotations

from pathlib import Path
from uuid import uuid4

from site_ai_widget.api.config import get_settings


def upload_root() -> Path:
    root = Path(get_settings().upload_dir)
    root.mkdir(parents=True, exist_ok=True)
    return root


def new_storage_name() -> str:
    return f"{uuid4()}.pdf"


def storage_path(storage_name: str) -> Path:
    """Resolve a stored file. Rejects path traversal."""
    name = Path(storage_name).name
    if name != storage_name or ".." in storage_name or "/" in storage_name or "\\" in storage_name:
        raise ValueError("invalid storage_name")
    path = upload_root() / name
    return path


def delete_storage_file(storage_name: str) -> None:
    path = storage_path(storage_name)
    path.unlink(missing_ok=True)
