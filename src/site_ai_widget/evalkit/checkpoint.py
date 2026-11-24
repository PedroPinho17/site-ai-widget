"""JSON checkpoint for eval resume. Never stores secrets."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

CHECKPOINT_NAME = ".checkpoint.json"


def checkpoint_path(eval_dir: Path) -> Path:
    return eval_dir / CHECKPOINT_NAME


def load_checkpoint(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {"done": [], "records": []}
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        return {"done": [], "records": []}
    done = [str(x) for x in data.get("done") or []]
    records = [r for r in (data.get("records") or []) if isinstance(r, dict)]
    return {"done": done, "records": records, "results_path": data.get("results_path")}


def save_checkpoint(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(path)


def clear_checkpoint(path: Path) -> None:
    path.unlink(missing_ok=True)
