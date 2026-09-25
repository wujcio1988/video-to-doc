"""Dysk cache dla niezmiennych etapów pipeline'u Video-to-Doc.

Cel: iteracja nad enrichmentem/renderem bez powtarzania whisper / ekstrakcji klatek / nano-banana.
Klucz etapu = fingerprint wideo (ścieżka+rozmiar+mtime) + parametry etapu — zmiana parametru
lub pliku wideo daje nowy klucz (brak ryzyka stale result).

Wyłączenie: --no-cache (CLI) lub VIDEO_TO_MANUAL_CACHE_DIR wskazuje katalog.
Uwaga: cache nano-banana podbija się przez NANO_CACHE_VERSION w image_enhancer.py.
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import tarfile
from pathlib import Path
from typing import Any, Dict, Optional


def default_cache_dir() -> Path:
    env = os.environ.get("VIDEO_TO_MANUAL_CACHE_DIR", "").strip()
    if env:
        return Path(env)
    return Path.home() / ".cache" / "vtd"


def video_fingerprint(video_path: Path) -> str:
    """Fingerprint pliku wideo: ścieżka + rozmiar + mtime (bez czytania zawartości)."""
    p = Path(video_path).resolve()
    st = p.stat()
    raw = f"{p}|{st.st_size}|{int(st.st_mtime)}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


def stage_key(*parts: Any) -> str:
    """Klucz etapu: sha256 z konkatenacji części (dowolne typy -> str)."""
    raw = "|".join(str(p) for p in parts)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:20]


def file_sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(262144), b""):
            h.update(chunk)
    return h.hexdigest()


def _stage_dir(cache_dir, stage: str, key: str) -> Path:
    return Path(cache_dir) / stage / key


def save_json(cache_dir, stage: str, key: str, obj: Any, name: str = "data.json") -> None:
    d = _stage_dir(cache_dir, stage, key)
    d.mkdir(parents=True, exist_ok=True)
    tmp = d / (name + ".tmp")
    tmp.write_text(json.dumps(obj, ensure_ascii=False), encoding="utf-8")
    tmp.replace(d / name)


def load_json(cache_dir, stage: str, key: str, name: str = "data.json") -> Optional[Any]:
    f = _stage_dir(cache_dir, stage, key) / name
    if not f.exists():
        return None
    try:
        return json.loads(f.read_text(encoding="utf-8"))
    except Exception:
        return None


def save_dir_tar(cache_dir, stage: str, key: str, src_dir: Path, name_prefix: Optional[str] = None) -> None:
    """Pakuje pliki z katalogu (opcjonalnie o prefiksie nazwy) do files.tar w cache."""
    d = _stage_dir(cache_dir, stage, key)
    d.mkdir(parents=True, exist_ok=True)
    tmp = d / "files.tar.tmp"
    with tarfile.open(tmp, "w") as tf:
        for f in sorted(Path(src_dir).iterdir()):
            if f.is_file() and (name_prefix is None or f.name.startswith(name_prefix)):
                tf.add(f, arcname=f.name)
    tmp.replace(d / "files.tar")


def restore_dir_tar(cache_dir, stage: str, key: str, dest_dir: Path) -> bool:
    """Rozpakowuje files.tar do dest_dir. True gdy tar istniał i rozpakowano."""
    tar_path = _stage_dir(cache_dir, stage, key) / "files.tar"
    if not tar_path.exists():
        return False
    dest = Path(dest_dir)
    dest.mkdir(parents=True, exist_ok=True)
    with tarfile.open(tar_path, "r") as tf:
        try:
            tf.extractall(dest, filter="data")
        except TypeError:  # Python < 3.12
            tf.extractall(dest)
    return True


def put_file(cache_dir, stage: str, key: str, src: Path, name: str) -> None:
    d = _stage_dir(cache_dir, stage, key)
    d.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, d / name)


def get_file(cache_dir, stage: str, key: str, name: str) -> Optional[Path]:
    f = _stage_dir(cache_dir, stage, key) / name
    return f if f.exists() else None


# ===== Wysokopoziomowe helpery dla etapów =====

def nano_lookup(cache_dir, key: Optional[str], dest_path: Path) -> Optional[Dict[str, Any]]:
    """Przywraca zaakceptowaną klatkę nano-banana z cache. Zwraca dict-res lub None."""
    if not cache_dir or not key:
        return None
    src = get_file(cache_dir, "nano", key, "enhanced.png")
    meta = load_json(cache_dir, "nano", key, "meta.json")
    if not src or not isinstance(meta, dict):
        return None
    dest_path = Path(dest_path)
    dest_path.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, dest_path)
    res = dict(meta)
    res["path"] = str(dest_path)
    res["cost_usd"] = 0.0
    res["cached"] = True
    return res


def nano_store(cache_dir, key: Optional[str], res: Dict[str, Any], enhanced_path: Path) -> None:
    """Zapisuje zaakceptowaną klatkę + metadane do cache."""
    if not cache_dir or not key:
        return
    enhanced_path = Path(enhanced_path)
    if not enhanced_path.exists():
        return
    put_file(cache_dir, "nano", key, enhanced_path, "enhanced.png")
    meta = {k: res.get(k) for k in ("status", "reason", "model", "target_ok", "via")}
    save_json(cache_dir, "nano", key, meta, "meta.json")
