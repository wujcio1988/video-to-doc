"""Testy transcribera + regresja GF-019 (word_timestamps usunięte) — mock faster_whisper."""
import sys
import types

import pytest

from vtd.core import transcriber as tr


@pytest.fixture()
def fake_whisper(monkeypatch):
    """Wstrzykuje atrapę faster_whisper i zwraca rejestr kwargs transkrypcji."""
    captured = {}

    class _FakeSeg:
        start = 0.0
        end = 2.5
        text = " Zaloguj się "
        words = []

    class _FakeModel:
        def __init__(self, size, device=None, compute_type=None):
            self.size = size

        def transcribe(self, path, **kwargs):
            captured.update(kwargs)
            captured["path"] = path
            return iter([_FakeSeg()]), None

    mod = types.ModuleType("faster_whisper")
    mod.WhisperModel = _FakeModel
    monkeypatch.setitem(sys.modules, "faster_whisper", mod)
    yield captured


def test_transcribe_segments(tmp_path, fake_whisper):
    t = tr.Transcriber(model_size="small", device="cpu", compute_type="int8")
    segs = t.transcribe(tmp_path / "a.wav", language="pl")
    assert len(segs) == 1
    assert segs[0].text == "Zaloguj się"
    assert segs[0].start == 0.0 and segs[0].end == 2.5


def test_gf019_regression_no_word_timestamps(tmp_path, fake_whisper):
    """GF-019: word_timestamps=True usunięte — narzut bez konsumpcji."""
    t = tr.Transcriber(model_size="small")
    t.transcribe(tmp_path / "a.wav")
    assert "word_timestamps" not in fake_whisper


def test_gf019_transcribe_args_present(tmp_path, fake_whisper):
    t = tr.Transcriber(model_size="small")
    t.transcribe(tmp_path / "a.wav", language="pl")
    assert fake_whisper["language"] == "pl"
    assert fake_whisper["vad_filter"] is True


def test_missing_library_raises_helpful_error(monkeypatch):
    monkeypatch.setitem(sys.modules, "faster_whisper", None)  # ImportError na imporcie
    with pytest.raises(ImportError, match="faster-whisper"):
        tr.Transcriber(model_size="small")


def test_format_timestamp():
    assert tr.format_timestamp(65.0) == "01:05"
    assert tr.format_timestamp(0.0) == "00:00"


def test_format_transcript_for_prompt():
    segs = [tr.SpeechSegment(0, 5, "A"), tr.SpeechSegment(70, 80, "B")]
    out = tr.format_transcript_for_prompt(segs)
    assert "[00:00 - 00:05] A" in out
    assert "[01:10 - 01:20] B" in out
