#!/usr/bin/env python3
"""steps.json — kanoniczny, samowystarczalny snapshot stanu pipeline (R1).

Moduł izoluje serializację/deserializację `steps.json`, aby `vtd run` mógł
zapisać pełny stan (ManualStep + enriched_data z LLM), a `vtd rerender` mógł
odtworzyć kroki bez parsowania MD/PROMPT.

Kontrakt:
  * `frame_path` w JSON jest ścieżką względną do katalogu wynikowego
    (przenośność — R2 planu); adapter odbudowuje ścieżki przy odczycie.
  * Brak `steps.json` = backward compat (stara logika rerender).
  * Żadne wartości nie zawierają sekretów (`sk-`, `Bearer `, `OPENAI_API_KEY`).
"""
import json
from pathlib import Path
from typing import Any, Dict, List, Optional

from vtd.core.fusion import ManualStep

STEPS_JSON_NAME = "steps.json"
SCHEMA_VERSION = "1.0"

# Wzorce potencjalnych sekretów (AC10). Sprawdzane tylko w danych serializowanych.
SECRET_MARKERS = ("OPENAI_API_KEY", "sk-", "Bearer ")


def _relative_frame(output_dir: Path, frame_path: Any) -> str:
    """Zwraca ścieżkę klatki względną do output_dir (albo oryginał, gdy już względna)."""
    p = Path(frame_path)
    if not p.is_absolute():
        return p.as_posix()
    try:
        return p.resolve().relative_to(Path(output_dir).resolve()).as_posix()
    except ValueError:
        # klatka poza katalogiem wynikowym — zapisz samą nazwę (bez wycieku ścieżki)
        return p.name


def _contains_secret(obj: Any) -> bool:
    """Rekurencyjnie sprawdza, czy w danych jest marker sekretu."""
    if isinstance(obj, str):
        return any(marker in obj for marker in SECRET_MARKERS)
    if isinstance(obj, dict):
        return any(_contains_secret(v) for v in obj.values())
    if isinstance(obj, (list, tuple)):
        return any(_contains_secret(v) for v in obj)
    return False


def _annotations_for(step_number: int, existing_ann_map: Optional[dict]) -> List[Any]:
    if not existing_ann_map:
        return []
    entry = existing_ann_map.get(step_number)
    if isinstance(entry, dict):
        anns = entry.get("annotations")
        if isinstance(anns, list):
            return anns
    return []


def save_steps_json(
    output_dir: Path,
    steps: list,
    enriched_data: Optional[dict],
    annotation_legend: Optional[dict] = None,
    existing_ann_map: Optional[dict] = None,
    version: str = SCHEMA_VERSION,
) -> Path:
    """Zapisuje steps.json do output_dir. Zwraca ścieżkę.

    Walidacja:
      1. Unikalne numery kroków (ValueError przy duplikacie).
      2. Brak sekretów w serializowanych danych (ValueError).
      3. Ścieżki klatek jako względne do output_dir.
    """
    output_dir = Path(output_dir)
    step_numbers = [s.step_number for s in steps]
    if len(set(step_numbers)) != len(step_numbers):
        duplicates = sorted({n for n in step_numbers if step_numbers.count(n) > 1})
        raise ValueError(
            f"steps.json: zduplikowane step_number {duplicates} — "
            "numery kroków muszą być unikalne"
        )

    steps_payload: List[Dict[str, Any]] = []
    for s in steps:
        steps_payload.append({
            "step_number": s.step_number,
            "start_time": s.start_time,
            "end_time": s.end_time,
            "frame_path": _relative_frame(output_dir, s.frame_path),
            "speech_text": s.speech_text,
            "grid_frames": [
                _relative_frame(output_dir, g)
                for g in (getattr(s, "grid_frames", None) or [])
            ],
            "annotations": _annotations_for(s.step_number, existing_ann_map),
        })

    payload: Dict[str, Any] = {
        "version": version,
        "steps": steps_payload,
        "enriched_data": enriched_data if enriched_data is not None else None,
        "annotation_legend": annotation_legend,
    }

    for field in ("steps", "enriched_data", "annotation_legend"):
        if _contains_secret(payload[field]):
            raise ValueError(
                f"steps.json: wykryto potencjalny sekret w polu '{field}' — "
                "zapis przerwany (ochrona klucza API)"
            )

    path = output_dir / STEPS_JSON_NAME
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    return path


def load_steps_json(output_dir: Path) -> Optional[dict]:
    """Wczytuje steps.json z output_dir. None gdy plik nie istnieje/uszkodzony (backward compat)."""
    path = Path(output_dir) / STEPS_JSON_NAME
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def steps_json_to_manual_steps(data: dict) -> List[ManualStep]:
    """Konwertuje data['steps'] → List[ManualStep] (frame_path jako Path z JSON)."""
    if not data:
        return []
    steps: List[ManualStep] = []
    for raw in data.get("steps", []) or []:
        steps.append(ManualStep(
            step_number=int(raw["step_number"]),
            start_time=float(raw["start_time"]),
            end_time=float(raw["end_time"]),
            frame_path=Path(raw["frame_path"]),
            speech_text=raw.get("speech_text", "") or "",
            grid_frames=[Path(g) for g in (raw.get("grid_frames") or [])],
        ))
    steps.sort(key=lambda s: s.step_number)
    return steps


def steps_json_to_enriched(data: dict) -> Optional[dict]:
    """Zwraca data.get('enriched_data') (może być None — zapis częściowy bez --enrich)."""
    if not data:
        return None
    return data.get("enriched_data")
