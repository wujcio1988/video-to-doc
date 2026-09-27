"""Testy frame_extractor + regresje GF-012 (timestampy) i GF-028 (sprzątanie starej sceny)."""
import subprocess
from unittest.mock import patch

import pytest

from vtd.core import frame_extractor


class _FakeRes:
    def __init__(self, stderr, returncode=0):
        self.stderr = stderr
        self.returncode = returncode


def _ffmpeg_stderr(pts_times):
    return "".join(f"frame n fps… showinfo … pts_time:{t}\n" for t in pts_times)


@pytest.fixture(autouse=True)
def _ffmpeg_available(monkeypatch):
    monkeypatch.setattr(frame_extractor.shutil, "which", lambda name: "/usr/bin/ffmpeg")
    monkeypatch.setattr(frame_extractor, "media_timeout", lambda *a, **kw: 10.0)


def test_gf012_regression_missing_pts_raises(tmp_path, monkeypatch):
    """GF-012: mniej wpisów showinfo niż klatek -> twardy błąd (nie fabrykowane timestampy)."""
    created = []

    def fake_run(cmd, **kw):
        # ffmpeg "wyciął" 2 klatki, ale showinfo zwrócił tylko 1 wpis
        for i in (1, 2):
            p = tmp_path / f"scene_{i:04d}.png"
            p.write_bytes(b"png")
            created.append(p)
        return _FakeRes(_ffmpeg_stderr([0.5]))

    monkeypatch.setattr(frame_extractor.subprocess, "run", fake_run)
    with pytest.raises(RuntimeError, match="timestamp"):
        frame_extractor.extract_scene_frames(tmp_path / "v.mkv", tmp_path)


def test_gf012_exact_match_ok(tmp_path, monkeypatch):
    def fake_run(cmd, **kw):
        for i in (1, 2):
            (tmp_path / f"scene_{i:04d}.png").write_bytes(b"png")
        return _FakeRes(_ffmpeg_stderr([0.5, 7.25]))

    monkeypatch.setattr(frame_extractor.subprocess, "run", fake_run)
    frames = frame_extractor.extract_scene_frames(tmp_path / "v.mkv", tmp_path)
    assert [f.timestamp for f in frames] == [0.5, 7.25]


def test_gf028_regression_stale_scene_files_removed(tmp_path, monkeypatch):
    """GF-012/028: stare scene_*.png są usuwane przed ekstrakcją."""
    stale = tmp_path / "scene_9999.png"
    stale.write_bytes(b"stary plik z poprzedniego runa")

    def fake_run(cmd, **kw):
        (tmp_path / "scene_0001.png").write_bytes(b"png")
        return _FakeRes(_ffmpeg_stderr([1.0]))

    monkeypatch.setattr(frame_extractor.subprocess, "run", fake_run)
    frames = frame_extractor.extract_scene_frames(tmp_path / "v.mkv", tmp_path)
    assert not stale.exists()
    assert len(frames) == 1


def test_timeout_maps_to_runtime_error(tmp_path, monkeypatch):
    monkeypatch.setattr(frame_extractor.subprocess, "run",
                        lambda *a, **kw: (_ for _ in ()).throw(subprocess.TimeoutExpired("ffmpeg", 10)))
    with pytest.raises(RuntimeError, match="limit czasu"):
        frame_extractor.extract_scene_frames(tmp_path / "v.mkv", tmp_path)


def test_extract_frame_at_failure_returns_none(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(frame_extractor.subprocess, "run",
                        lambda *a, **kw: _FakeRes("", returncode=1))
    out = tmp_path / "x.png"
    assert frame_extractor.extract_frame_at(tmp_path / "v.mkv", 1.0, out) is None
    assert not out.exists()
    assert "Ostrzeżenie" in capsys.readouterr().out
