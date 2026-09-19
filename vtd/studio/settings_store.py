"""
Zarządzanie trwałymi ustawieniami Video-to-Doc Studio.
"""
from __future__ import annotations

import json
import threading
from pathlib import Path
from typing import Any, Dict

from vtd.config import load_config

_lock = threading.Lock()

DEFAULT_MODELS = [
    "gpt-4o-mini",
    "gpt-4o",
    "claude-3-5-sonnet",
    "deepseek-chat",
    "qwen-2.5-coder-32b",
    "custom",
]

ALLOWED_MODELS = DEFAULT_MODELS

DEFAULTS: Dict[str, Any] = {
    "enrich": False,
    "enrich_model": "gpt-4o-mini",
    "scene_threshold": 0.05,
    "track": "auto",
    "author": "",
    "pipeline_python": "",
    "frame_qa": False,
    "max_step_seconds": 45.0,
    "grid_interval": 30.0,
    "annotate": False,
    "nano_banana": False,
}


def get_settings_path() -> Path:
    cfg = load_config()
    p = cfg.storage.data_dir / "settings.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    return p


def load_settings() -> Dict[str, Any]:
    with _lock:
        sp = get_settings_path()
        if not sp.is_file():
            return dict(DEFAULTS)
        try:
            data = json.loads(sp.read_text(encoding="utf-8"))
            merged = dict(DEFAULTS)
            merged.update(data)
            return merged
        except Exception:
            return dict(DEFAULTS)


def save_settings(data: Dict[str, Any]) -> Dict[str, Any]:
    with _lock:
        merged = dict(DEFAULTS)
        for k in DEFAULTS:
            if k in data:
                merged[k] = data[k]
        sp = get_settings_path()
        sp.write_text(json.dumps(merged, indent=2, ensure_ascii=False), encoding="utf-8")
        return merged
