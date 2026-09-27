"""Testy fusion + regresja GF-023 (jawny komunikat przy braku gridu, bez martwych gałęzi)."""
import pytest

from vtd.core.dedup import ExtractedFrame
from vtd.core.audio_extractor import SilenceInterval
from vtd.core.transcriber import SpeechSegment
from vtd.core import fusion


def _frame(t, name, tmp_path):
    return ExtractedFrame(timestamp=t, path=tmp_path / name)


def test_basic_fusion_one_step(tmp_path):
    frames = [_frame(0.0, "scene_0001.png", tmp_path)]
    steps = fusion.fuse_signals_into_steps_v2(frames, [], [SpeechSegment(0, 8, "Robimy A")],
                                              video_duration=10.0)
    assert len(steps) == 1
    assert steps[0].start_time == 0.0
    assert "Robimy A" in steps[0].speech_text


def test_fusion_respects_max_step_split(tmp_path):
    frames = [_frame(0.0, "scene_0001.png", tmp_path)]
    transcripts = [
        SpeechSegment(0, 20, "Czesc A"),
        SpeechSegment(50, 70, "Czesc B"),
    ]
    steps = fusion.fuse_signals_into_steps_v2(
        frames, [], transcripts, video_duration=100.0,
        max_step_seconds=45.0, min_step_seconds=1.0,
    )
    assert len(steps) >= 2
    # monotoniczność
    starts = [s.start_time for s in steps]
    assert starts == sorted(starts)


def test_validate_steps_timestamps_negative_range(tmp_path):
    steps = [fusion.ManualStep(step_number=1, start_time=10.0, end_time=5.0,
                               frame_path=tmp_path / "f.png", speech_text="")]
    warnings = fusion.validate_steps_timestamps(steps)
    assert any("ujemny" in w for w in warnings)


def test_empty_frames_returns_empty():
    assert fusion.fuse_signals_into_steps_v2([], [], []) == []


def test_gf023_placeholder_grid_without_video_path(tmp_path):
    """Krok dłuższy niż grid_interval bez video_path -> placeholder w grid_frames (zachowane zachowanie)."""
    frames = [_frame(0.0, "scene_0001.png", tmp_path)]
    steps = fusion.fuse_signals_into_steps_v2(
        frames, [], [SpeechSegment(0, 10, "Opis")], video_duration=70.0, grid_interval=30.0
    )
    assert len(steps) == 1
    assert steps[0].grid_frames, "krok > grid_interval bez wewnętrznej klatki powinien mieć grid"


def test_gf023_regression_failed_grid_extraction_warns(tmp_path, capsys, monkeypatch):
    """GF-023: nieudana ekstrakcja gridu -> jawne ostrzeżenie + krok BEZ gridu (nie cichy brak)."""
    import vtd.core.frame_extractor as fe
    frames = [_frame(0.0, "scene_0001.png", tmp_path)]
    monkeypatch.setattr(fe, "extract_frame_at", lambda *a, **kw: None)
    steps = fusion.fuse_signals_into_steps_v2(
        frames, [], [SpeechSegment(0, 10, "Opis")], video_duration=70.0,
        grid_interval=30.0, video_path=tmp_path / "fake.mkv", frames_dir=tmp_path,
    )
    assert steps[0].grid_frames == []
    out = capsys.readouterr().out
    assert "BEZ gridu" in out


def test_gf023_no_dead_pass_branch():
    """GF-023: martwa gałąź `pass` usunięta z fusion (weryfikacja źródła)."""
    import inspect
    src = inspect.getsource(fusion)
    # dedenty dead-branch z oryginału (274-276 i 291-292) nie istnieją
    assert "already handled" not in src
    assert "-> no grid" not in src
