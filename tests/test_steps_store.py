"""Testy vtd.core.steps_store (R1) — T1, T2 + AC10 (brak sekretów)."""
from pathlib import Path

import pytest

from vtd.core.fusion import ManualStep
from vtd.core import steps_store


def _mk_steps(output_dir: Path):
    return [
        ManualStep(
            step_number=1,
            start_time=0.0,
            end_time=12.5,
            frame_path=output_dir / "frames" / "scene_0001.png",
            speech_text="Otwieramy moduł Produkcja.",
            grid_frames=[output_dir / "frames" / "scene_0002.png"],
        ),
        ManualStep(
            step_number=2,
            start_time=12.5,
            end_time=28.0,
            frame_path=output_dir / "frames" / "scene_0003.png",
            speech_text="Klikamy Dodaj.",
        ),
    ]


ENRICHED = {
    "meta": {
        "client": "LANGSTEINER",
        "process": "Produkcja by CTI",
        "module": "Panel Produkcyjny",
        "environment": "Wish_Testy",
        "author": "Artur",
        "document_status": "DRAFT",
        "mode": "manual",
    },
    "intro": "Instrukcja obsługi Panelu Produkcyjnego.",
    "steps": [
        {
            "step_number": 1,
            "title": "Lista zleceń produkcyjnych",
            "description": "Otwórz moduł Produkcja i przejdź do listy ZP.",
            "callout_type": "tip",
            "callout_title": "Wskazówka",
            "callout_text": "Upewnij się, że wybrana jest właściwa baza.",
        },
        {
            "step_number": 2,
            "title": "Dodanie zlecenia produkcyjnego",
            "description": "Kliknij Dodaj i uzupełnij technologię.",
            "callout_type": "warning",
            "callout_title": "Uwaga",
            "callout_text": "Technologia musi być aktywna.",
        },
    ],
}


# T1 — round-trip save -> load bez utraty danych
def test_steps_store_save_and_load_roundtrip(tmp_path):
    steps = _mk_steps(tmp_path)
    saved_path = steps_store.save_steps_json(
        output_dir=tmp_path,
        steps=steps,
        enriched_data=ENRICHED,
        annotation_legend=None,
        existing_ann_map={
            1: {"step_number": 1, "annotations": [{"type": "badge", "number": 1, "label": "Dodaj"}]}
        },
    )
    assert saved_path == tmp_path / "steps.json"
    assert saved_path.exists()

    data = steps_store.load_steps_json(tmp_path)
    assert data is not None
    assert data["version"] == "1.0"
    assert data["steps"][0]["step_number"] == 1
    assert data["enriched_data"]["meta"]["document_status"] == "DRAFT"

    # round-trip kroków: step_number/start/end/speech + frame_path względny
    back = steps_store.steps_json_to_manual_steps(data)
    assert [s.step_number for s in back] == [1, 2]
    assert (back[0].start_time, back[0].end_time) == (0.0, 12.5)
    assert (back[1].start_time, back[1].end_time) == (12.5, 28.0)
    assert back[0].speech_text == "Otwieramy moduł Produkcja."
    assert back[1].speech_text == "Klikamy Dodaj."
    # frame_path zapisany względnie (przenośny JSON) i odczytany jako Path
    assert data["steps"][0]["frame_path"] == "frames/scene_0001.png"
    assert back[0].frame_path == Path("frames/scene_0001.png")
    assert back[0].grid_frames == [Path("frames/scene_0002.png")]
    assert data["steps"][0]["annotations"][0]["label"] == "Dodaj"

    # adapter enriched
    assert steps_store.steps_json_to_enriched(data)["steps"][1]["title"] == "Dodanie zlecenia produkcyjnego"


# T2 — duplikat step_number -> wyjątek przy zapisie
def test_steps_store_duplicate_step_number_raises(tmp_path):
    steps = [
        ManualStep(1, 0.0, 5.0, tmp_path / "frames" / "a.png", ""),
        ManualStep(1, 5.0, 10.0, tmp_path / "frames" / "b.png", ""),
    ]
    with pytest.raises(ValueError):
        steps_store.save_steps_json(tmp_path, steps, ENRICHED)


# T7 (AC10) — brak sekretów: marker w enriched_data -> zapis przerwany
def test_steps_store_rejects_secrets(tmp_path):
    steps = _mk_steps(tmp_path)
    poisoned = {
        "meta": {"document_status": "DRAFT"},
        "intro": "klucz: sk-abc123",
        "steps": [],
    }
    with pytest.raises(ValueError):
        steps_store.save_steps_json(tmp_path, steps, poisoned)
    assert not (tmp_path / "steps.json").exists(), "zapis z sekretem nie może powstać"


# R5/R1 — zapis częściowy: enriched_data=None => enriched_data: null
def test_steps_store_enriched_none_is_null(tmp_path):
    steps = _mk_steps(tmp_path)
    steps_store.save_steps_json(tmp_path, steps, enriched_data=None)
    data = steps_store.load_steps_json(tmp_path)
    assert data is not None
    assert data["enriched_data"] is None
    assert steps_store.steps_json_to_enriched(data) is None


# backward compat — brak pliku => None
def test_steps_store_load_missing_returns_none(tmp_path):
    assert steps_store.load_steps_json(tmp_path) is None
