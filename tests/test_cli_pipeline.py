"""Testy test_cli_pipeline — smoke run_pipeline na mockach/fixturach (bez sieci/GPU/ffmpeg).

Regresje: GF-008 (jawny fallback toru audio), GF-025 (PipelineError zamiast sys.exit),
GF-006/026 (pipeline przechodzi po porządkach).
"""
import json
from pathlib import Path
from unittest.mock import patch

import pytest

from vtd import cli
from vtd.core.dedup import ExtractedFrame
from vtd.core.transcriber import SpeechSegment


def _make_png(path):
    from PIL import Image
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (64, 64), (90, 90, 90)).save(path, "PNG")
    return path


class _FakeTranscriber:
    def __init__(self, model_size=None, device=None, compute_type=None):
        pass

    def transcribe(self, wav_path, language="pl"):
        return [SpeechSegment(0.0, 8.0, "Otwórz moduł"), SpeechSegment(9.0, 15.0, "Kliknij Zapisz")]


@pytest.fixture()
def video(tmp_path):
    v = tmp_path / "nagranie.mkv"
    v.write_bytes(b"fakewideo")
    return v


@pytest.fixture()
def mocked_env(monkeypatch, tmp_path):
    """Mockuje wszystkie zależności zewnętrzne run_pipeline; zwraca monkeypatch."""
    monkeypatch.setattr(cli, "get_audio_stream_count", lambda p: 2)
    monkeypatch.setattr(cli, "extract_mic_audio", lambda *a, **kw: None)
    monkeypatch.setattr(cli, "detect_silence", lambda *a, **kw: [])
    monkeypatch.setattr(cli, "Transcriber", _FakeTranscriber)

    def fake_extract(video_path, frames_dir, threshold=0.35):
        f1 = _make_png(frames_dir / "scene_0001.png")
        f2 = _make_png(frames_dir / "scene_0002.png")
        return [ExtractedFrame(0.0, f1), ExtractedFrame(12.0, f2)]

    monkeypatch.setattr(cli, "extract_scene_frames", fake_extract)
    monkeypatch.setattr(cli, "filter_duplicate_frames", lambda frames, **kw: frames)

    def fake_docx(title, steps, output_docx, **kw):
        output_docx.write_bytes(b"docx")

    monkeypatch.setattr(cli, "render_docx_manual", fake_docx)
    yield monkeypatch


def test_smoke_run_pipeline_produces_artifacts(video, tmp_path, mocked_env):
    out = tmp_path / "out"
    steps = cli.run_pipeline(video_path=video, output_dir=out, title="Test")
    assert len(steps) == 2
    for fname in ("INSTRUKCJA.md", "INSTRUKCJA.html", "INSTRUKCJA.docx", "TRANSKRYPCJA.md",
                  "metadata.json", "manifest.json", "COMPLETENESS_REPORT.md",
                  "PROMPT_DLA_AGENTA.txt", "steps.json"):
        assert (out / fname).exists(), f"brakuje artefaktu {fname}"
    assert (out / "client-ready" / "INSTRUKCJA.md").exists()
    metadata = json.loads((out / "metadata.json").read_text(encoding="utf-8"))
    assert metadata["enrich_status"] == "not_requested"
    assert metadata["document_status"] == "DRAFT"
    manifest = json.loads((out / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["document_status"] == "DRAFT"


def test_r1_steps_json_saved_after_run(video, tmp_path, mocked_env):
    """R1/AC2: po vtd run w output_dir istnieje steps.json z krokami (zapis częściowy bez --enrich)."""
    out = tmp_path / "out_steps"
    cli.run_pipeline(video_path=video, output_dir=out, title="T")
    steps_path = out / "steps.json"
    assert steps_path.exists()
    data = json.loads(steps_path.read_text(encoding="utf-8"))
    assert data["version"] == "1.0"
    assert [s["step_number"] for s in data["steps"]] == [1, 2]
    assert data["enriched_data"] is None, "bez --enrich zapis częściowy: enriched_data=null"
    assert all(not Path(s["frame_path"]).is_absolute() for s in data["steps"]), \
        "frame_path w steps.json musi być względny (przenośność)"


def test_gf025_regression_missing_video_raises_pipeline_error(tmp_path):
    """GF-025: brak pliku -> PipelineError (wyjątek), NIE sys.exit(1)."""
    with pytest.raises(cli.PipelineError):
        cli.run_pipeline(video_path=tmp_path / "brak.mkv", output_dir=tmp_path / "o", title="T")


def test_gf025_regression_cli_main_maps_to_exit_code(video, tmp_path, mocked_env, capsys):
    """GF-025: warstwa CLI mapuje PipelineError na kod wyjścia 1."""
    with pytest.raises(SystemExit) as ei:
        cli.main(["run", str(tmp_path / "brak.mkv"), "--output", str(tmp_path / "o")])
    assert ei.value.code == 1
    assert "BŁĄD" in capsys.readouterr().out


def test_gf008_regression_track2_fallback_warns(video, tmp_path, mocked_env, capsys):
    """GF-008: żądany Track 3 przy 2 strumieniach -> jawne ostrzeżenie + fallback na miks."""
    calls = {}
    mocked_env.setattr(cli, "get_audio_stream_count", lambda p: 2)
    mocked_env.setattr(cli, "extract_mic_audio",
                       lambda p, w, track_index=0: calls.setdefault("track", track_index))
    out = tmp_path / "out2"
    cli.run_pipeline(video_path=video, output_dir=out, title="T", track="2")
    assert "OSTRZEŻENIE" in capsys.readouterr().out
    assert calls["track"] == 0, "fallback na tor 0 (miks) — ale jawnie raportowany"


def test_gf008_track2_available_uses_it(video, tmp_path, mocked_env, capsys):
    mocked_env.setattr(cli, "get_audio_stream_count", lambda p: 3)
    calls = {}
    mocked_env.setattr(cli, "extract_mic_audio",
                       lambda p, w, track_index=0: calls.setdefault("track", track_index))
    cli.run_pipeline(video_path=video, output_dir=tmp_path / "o3", title="T", track="2")
    assert calls["track"] == 2
    assert "OSTRZEŻENIE" not in capsys.readouterr().out


def test_gf022_speech_text_clean_in_docs(video, tmp_path, mocked_env):
    """GF-007 (integracyjnie): werdykty QA nie wyciekają do speech_text w dokumentach."""
    out = tmp_path / "out4"
    steps = cli.run_pipeline(video_path=video, output_dir=out, title="T")
    assert all("[Frame QA" not in s.speech_text for s in steps)
    md = (out / "INSTRUKCJA.md").read_text(encoding="utf-8")
    assert "[Frame QA" not in md


def test_gf022_regression_hard_max_reviews_limit(video, tmp_path, mocked_env):
    """GF-022: twardy limit MAX_REVIEWS — kroki po limicie dostają 'skipped', nie ciche przekroczenie."""
    import vtd.core.frame_qa as fq

    class _FakeReviewer:
        def review_step_frame(self, step_number, title, desc, frame_path):
            return {"verdict": "ok", "reason": ""}

    mocked_env.setattr(fq, "FrameReviewer", lambda: _FakeReviewer())
    mocked_env.setattr(fq, "MAX_REVIEWS", 1)
    mocked_env.setattr(fq, "improve_step_frame", lambda *a, **kw: None)
    out = tmp_path / "out_qa"
    cli.run_pipeline(video_path=video, output_dir=out, title="T", frame_qa=True)
    metadata = json.loads((out / "metadata.json").read_text(encoding="utf-8"))
    results = metadata["frame_qa_results"]
    reviewed = [r for r in results if r["verdict"] == "ok"]
    skipped = [r for r in results if r["verdict"] == "skipped"]
    assert len(reviewed) == 1, "tylko MAX_REVIEWS review"
    assert len(skipped) >= 1, "kroki poza limitem jawnie 'skipped' (nie ciche przekroczenie)"
    assert len(results) == 2, "każdy krok ma jawnie zapisany wynik QA"


def test_meeting_mode_disables_annotate(video, tmp_path, mocked_env):
    out = tmp_path / "out5"
    metadata = {}
    cli.run_pipeline(video_path=video, output_dir=out, title="T", mode="meeting", annotate=True)
    metadata = json.loads((out / "metadata.json").read_text(encoding="utf-8"))
    assert metadata["annotate"] is False


def test_ds001_regression_annotate_reaches_plan_annotations(video, tmp_path, mocked_env, monkeypatch, capsys):
    """DS-001: --annotate realnie dochodzi do plan_annotations (TypeError był połykany
    przez except -> adnotacje nigdy nie powstawały). Faker wiernie odzwierciedla
    KANONICZNĄ sygnaturę plan_annotations(model=) — ponowne wprowadzenie złego kwargsa
    (np. llm_model=) wywali test TypeError-em, tak jak w produkcji."""
    import inspect

    import vtd.core.element_locator as el

    real_signature = inspect.signature(el.plan_annotations)
    calls = []

    def strict_fake(step_number, step_title, step_description, located, model=None):
        """Mirror sygnatury wewnętrznej plan_annotations — any obcy kwarg -> TypeError."""
        inspect.signature(el.plan_annotations).bind(
            step_number, step_title, step_description, located, model=model
        )
        calls.append({"step_number": step_number, "model": model})
        return []

    # bez OCR (tesseract offline) i bez LLM: located={} -> deterministyczny flow
    monkeypatch.setattr(el, "locate_words", lambda p, words: {})
    monkeypatch.setattr(el, "plan_annotations", strict_fake)
    out = tmp_path / "out_ann"
    steps = cli.run_pipeline(video_path=video, output_dir=out, title="T", annotate=True)
    assert calls, "plan_annotations nie zostało wywołane — ścieżka adnotacji przerwana (DS-001)"
    assert sorted(c["step_number"] for c in calls) == [1, 2]
    assert all(c["model"] is None for c in calls), (
        "cli nie powinno wymuszać modelu — plan_annotations wybiera cfg.llm.element_locator_model"
    )
    assert "Błąd globalny adnotacji" not in capsys.readouterr().out, (
        "TypeError/KwargError był połykany — ścieżka adnotacji ze złym kwargsem"
    )
    assert real_signature is not None


def test_ds003_regression_qa_model_not_hardcoded_in_cli(video, tmp_path, mocked_env):
    """DS-003: cli NIE nadpisuje modelu QA zaszytą wartością — decyduje
    cfg.llm.qa_verifier_model (backend qa_verifier sam czyta config)."""
    import vtd.core.enricher as en
    import vtd.core.qa_verifier as qv

    audit_calls = []

    def fake_audit(*args, **kwargs):
        audit_calls.append(kwargs)
        return kwargs["enriched_data"], []

    def fake_enrich(*args, **kwargs):
        return {"meta": {}, "steps": []}

    mocked_env.setattr(qv, "audit_and_fix_manual", fake_audit)
    mocked_env.setattr(en, "enrich_steps_with_llm", fake_enrich)
    out = tmp_path / "out_qa_model"
    cli.run_pipeline(video_path=video, output_dir=out, title="T", enrich=True)
    assert audit_calls, "qa_verifier.audit_and_fix_manual nie zostało wywołane"
    call_kwargs = audit_calls[0]
    assert "model" not in call_kwargs or call_kwargs["model"] is None, (
        "cli nie powinno narzucać modelu QA — ma wygrywać konfiguracja (DS-003)"
    )
    source = cli.__file__ and open(cli.__file__, encoding="utf-8").read()
    assert "antigravity" not in source, "identyfikator gatewaya wciąż zaszyty w kodzie (DS-003)"
