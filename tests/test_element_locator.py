from pathlib import Path
from unittest.mock import patch, MagicMock
from vtd.core.element_locator import plan_annotations


def test_plan_annotations_fallback():
    located = {
        "zapisz": [100, 100, 150, 120],
        "anuluj": [200, 100, 250, 120],
    }
    # Bez LLM (lub błąd LLM) fallback wybiera słowo ze step_description
    plan = plan_annotations(
        step_number=1,
        step_title="Zapis danych",
        step_description="Kliknij przycisk Zapisz w formularzu",
        located=located,
    )
    assert len(plan) == 1
    assert plan[0]["type"] == "arrow"
    assert plan[0]["element"] == "zapisz"
    assert plan[0]["box"] == [100, 100, 150, 120]


def test_plan_annotations_empty():
    plan = plan_annotations(1, "Tytuł", "Opis", {})
    assert plan == []
