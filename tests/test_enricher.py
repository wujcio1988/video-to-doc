"""Testy enrichera + regresje GF-007 (QA bez mutacji speech_text), GF-020 (martwy timeout), GF-021 (cache OCR)."""
import inspect
from pathlib import Path
from unittest.mock import patch

import pytest

from vtd.core import enricher
from vtd.core.fusion import ManualStep


def _mk_step(num, text, tmp_path):
    frame = tmp_path / f"scene_{num:04d}.png"
    frame.write_bytes(b"\x89PNG\r\n\x1a\n")
    return ManualStep(step_number=num, start_time=(num - 1) * 10.0, end_time=num * 10.0,
                      frame_path=frame, speech_text=text)


class _CaptureProvider:
    """Zwraca poprawny JSON enriched; przechwytuje messages do asercji na prompt."""

    last_messages = None

    def chat_completion(self, messages, **kwargs):
        type(self).last_messages = messages
        return (
            '{"meta": {}, "intro": "Cel.", '
            '"steps": [{"step_number": 1, "title": "T", "description": "D", '
            '"callout_type": "tip", "callout_text": "C"}]}'
        )


def _call_enricher(steps, qa_results=None, tmp_path=None):
    with patch.object(enricher, "load_config"), \
         patch.object(enricher, "get_llm_provider", return_value=_CaptureProvider()):
        return enricher.enrich_steps_with_llm(
            title="Test", steps=steps, model="m",
            frame_qa_results=qa_results,
        )


def test_gf020_timeout_param_removed():
    """GF-020: martwy parametr timeout usunięty z API enrichera."""
    params = inspect.signature(enricher.enrich_steps_with_llm).parameters
    assert "timeout" not in params


def test_gf007_regression_speech_text_not_mutated(tmp_path):
    """GF-007: werdykt QA NIE jest dopisywany do step.speech_text."""
    s = _mk_step(1, "Kliknij Zapisz", tmp_path)
    original = s.speech_text
    qa = [{"step_number": 1, "verdict": "unclear", "reason": "rozmyta klatka"}]
    _call_enricher([s], qa_results=qa)
    assert s.speech_text == original
    assert "Frame QA" not in s.speech_text


def test_gf007_qa_context_in_prompt(tmp_path):
    """GF-007: kontekst QA idzie do promptu osobnym wierszem."""
    s = _mk_step(1, "Kliknij Zapisz", tmp_path)
    qa = [{"step_number": 1, "verdict": "unclear", "reason": "rozmyta klatka"}]
    _call_enricher([s], qa_results=qa)
    user_msg = _CaptureProvider.last_messages[-1]["content"]
    assert "QA KLATKI" in user_msg
    assert "rozmyta klatka" in user_msg


def test_enricher_returns_parsed_json(tmp_path):
    s = _mk_step(1, "Krok", tmp_path)
    result = _call_enricher([s])
    assert result is not None
    assert result["steps"][0]["step_number"] == 1


def test_gf021_ocr_cache(tmp_path, monkeypatch):
    """GF-021: OCR tej samej klatki wywołany wielokrotnie -> jeden subprocess."""
    enricher._ocr_cache.clear()
    frame = tmp_path / "scene_0001.png"
    frame.write_bytes(b"\x89PNG\r\n\x1a\n")
    calls = {"n": 0}

    class _R:
        stdout = "linia1\nlinia2\n"

    def fake_run(*a, **kw):
        calls["n"] += 1
        return _R()

    monkeypatch.setattr(enricher.subprocess, "run", fake_run)
    t1 = enricher.extract_frame_ocr(frame)
    t2 = enricher.extract_frame_ocr(frame)
    assert calls["n"] == 1
    assert t1 == t2 == "linia1\nlinia2"


def test_gf021_ocr_cache_invalidated_on_mtime_change(tmp_path, monkeypatch):
    enricher._ocr_cache.clear()
    frame = tmp_path / "scene_0001.png"
    frame.write_bytes(b"x")
    calls = {"n": 0}

    class _R:
        stdout = "A\n"

    def fake_run(*a, **kw):
        calls["n"] += 1
        return _R()

    monkeypatch.setattr(enricher.subprocess, "run", fake_run)
    enricher.extract_frame_ocr(frame)
    # zmiana mtime symuluje nadpisanie pliku
    import os
    os.utime(frame, (time_ := 1_000_000_000, time_))
    enricher.extract_frame_ocr(frame)
    assert calls["n"] == 2
    enricher._ocr_cache.clear()


def test_enricher_llm_error_returns_none(tmp_path, capsys):
    s = _mk_step(1, "Krok", tmp_path)

    class _ErrProvider:
        def chat_completion(self, **kw):
            raise RuntimeError("down")

    with patch.object(enricher, "load_config"), \
         patch.object(enricher, "get_llm_provider", return_value=_ErrProvider()):
        result = enricher.enrich_steps_with_llm(title="T", steps=[s], model="m")
    assert result is None
    assert "OSTRZEŻENIE" in capsys.readouterr().out
