"""Testy audio_detection — parsowanie wyjść ffmpeg na fiksaturach, bez sieci/GPU."""
import subprocess
from unittest.mock import patch

from vtd.core import audio_detection as ad

STDERR_SPEECH = """
[Parsed_volumedetect_0] mean_volume: -20.5 dB
[Parsed_volumedetect_0] max_volume: -3.1 dB
[silencedetect @ 0x0] silence_start: 0.1
[silencedetect @ 0x0] silence_end: 0.5 | silence_duration: 0.4
"""

STDERR_SILENT = """
[Parsed_volumedetect_0] mean_volume: -91.0 dB
[Parsed_volumedetect_0] max_volume: -91.0 dB
"""


def test_measure_track_parses_speech():
    def fake_run(cmd, **kw):
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr=STDERR_SPEECH)

    with patch.object(ad.subprocess, "run", fake_run):
        m = ad._measure_track("v.mkv", 0, "/usr/bin/ffmpeg", 60.0)
    assert m["mean_volume_db"] == -20.5
    assert m["max_volume_db"] == -3.1
    assert m["speech_ratio"] > 0.05
    assert not m.get("limited")


def test_measure_track_limit_120s():
    def fake_run(cmd, **kw):
        assert "-t" in cmd and "120" in cmd, "długie nagranie mierzone z limitem 120s"
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr=STDERR_SPEECH)

    with patch.object(ad.subprocess, "run", fake_run):
        m = ad._measure_track("v.mkv", 0, "/usr/bin/ffmpeg", 500.0)
    assert m.get("limited") is True


def test_measure_track_timeout_is_safe():
    def fake_run(cmd, **kw):
        raise subprocess.TimeoutExpired("ffmpeg", 120)

    with patch.object(ad.subprocess, "run", fake_run):
        m = ad._measure_track("v.mkv", 0, "/usr/bin/ffmpeg", 60.0)
    assert m["speech_ratio"] == 0.0
    assert m["mean_volume_db"] == -91.0


def test_measure_track_silent():
    def fake_run(cmd, **kw):
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr=STDERR_SILENT)

    with patch.object(ad.subprocess, "run", fake_run):
        m = ad._measure_track("v.mkv", 0, "/usr/bin/ffmpeg", 60.0)
    assert m["speech_ratio"] < 0.01


def test_index_to_label_mapping():
    assert ad._INDEX_TO_LABEL == {0: "mix", 1: "mic", 2: "2"}


def test_no_ffmpeg_warns_no_exception(tmp_path):
    with patch.object(ad, "_resolve_ffmpeg", return_value=None):
        r = ad.detect_best_track(str(tmp_path / "v.mkv"))
    assert r["best_track_index"] is None
    assert "ffmpeg" in r["warning"]
    assert r["tracks"] == []


def test_missing_file_warns(tmp_path):
    with patch.object(ad, "_resolve_ffmpeg", return_value="/usr/bin/ffmpeg"), \
         patch.object(ad, "_resolve_ffprobe", return_value="/usr/bin/ffprobe"):
        r = ad.detect_best_track(str(tmp_path / "brak.mkv"))
    assert "plik" in r["warning"]


def test_detect_best_track_two_streams(tmp_path):
    """Dwa tory: tor 1 z mową, tor 0 cichy -> best=1 (mic)."""
    video = tmp_path / "v.mkv"
    video.write_bytes(b"fakewideo")

    def fake_probe(*a, **kw):
        return 100.0

    def fake_streams(*a, **kw):
        return 2

    responses = {0: STDERR_SILENT, 1: STDERR_SPEECH}

    def fake_run(cmd, **kw):
        idx = cmd[cmd.index("-map") + 1]
        n = int(idx.split(":")[-1])
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr=responses[n])

    with patch.object(ad, "_probe_duration", fake_probe), \
         patch.object(ad, "_get_n_streams", fake_streams), \
         patch.object(ad.subprocess, "run", fake_run):
        r = ad.detect_best_track(str(video))
    assert r["n_streams"] == 2
    assert r["best_track_index"] == 1
    assert r["best_track_label"] == "mic"
    assert r["has_speech"] is True
    assert r["tracks"][0]["verdict"] == "silent"
