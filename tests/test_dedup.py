"""Testy dedup + regresja GF-011 (jawne ostrzeżenie przy braku bibliotek, okno porównań)."""
import sys

import pytest

from vtd.core.dedup import ExtractedFrame, filter_duplicate_frames
from PIL import Image, ImageDraw


def _mk_png(path, pattern):
    """Wzorzec 'dot' lub 'diag' — pomiędzy nimi duży dystans pHash (12 > threshold 8)."""
    img = Image.new("L", (64, 64), 0)
    d = ImageDraw.Draw(img)
    if pattern == "dot":
        d.ellipse([16, 16, 47, 47], fill=255)
    else:  # diag
        for i in range(64):
            d.point((i, i), fill=255)
    img.save(path, "PNG")
    return ExtractedFrame(timestamp=0.0, path=path)


def _frames(tmp_path):
    f1 = _mk_png(tmp_path / "a.png", "dot")
    f2 = _mk_png(tmp_path / "b.png", "diag")
    f1.timestamp = 0.0
    f2.timestamp = 10.0
    return [f1, f2]


def test_dedup_keeps_distinct_frames(tmp_path):
    frames = _frames(tmp_path)
    result = filter_duplicate_frames(frames, hamming_threshold=8)
    assert len(result) == 2


def test_dedup_removes_duplicates(tmp_path):
    f1 = _mk_png(tmp_path / "a.png", "h")
    f2 = _mk_png(tmp_path / "b.png", "h")  # ten sam wzorzec -> niemal identyczny pHash
    f1.timestamp, f2.timestamp = 0.0, 10.0
    result = filter_duplicate_frames([f1, f2], hamming_threshold=8)
    assert len(result) == 1
    assert result[0].path == f1.path


def test_dedup_respects_min_time_delta(tmp_path):
    f1 = _mk_png(tmp_path / "a.png", "dot")
    f2 = _mk_png(tmp_path / "b.png", "diag")
    f1.timestamp, f2.timestamp = 0.0, 0.5  # zbyt gęsto
    result = filter_duplicate_frames([f1, f2], min_time_delta=1.5)
    assert len(result) == 1


def test_empty_input():
    assert filter_duplicate_frames([]) == []


def test_gf011_regression_missing_library_warns(tmp_path, monkeypatch, capsys):
    """GF-011: brak imagehash/PIL -> jawne ostrzeżenie, nie ciche wyłączenie."""
    frames = _frames(tmp_path)
    monkeypatch.setitem(sys.modules, "imagehash", None)  # import rzuci ImportError
    result = filter_duplicate_frames(frames)
    assert result == frames, "bez bibliotek zwracane są wszystkie klatki"
    out = capsys.readouterr().out
    assert "OSTRZEŻENIE" in out
    assert "WYŁĄCZONA" in out


def test_gf011_window_comparison_catches_drift(tmp_path):
    """GF-011: porównanie z oknem — klatka podobna do starszej z okna jest odrzucana."""
    fa = _mk_png(tmp_path / "a.png", "dot")
    fb = _mk_png(tmp_path / "b.png", "diag")
    fc = _mk_png(tmp_path / "c.png", "dot")  # dryf: podobny do A, nie do B
    fa.timestamp, fb.timestamp, fc.timestamp = 0.0, 10.0, 20.0
    result = filter_duplicate_frames([fa, fb, fc], hamming_threshold=8, compare_window=5)
    paths = [f.path.name for f in result]
    assert "c.png" not in paths, "okno hashy powinno wychwycić powrót do wzorca A"
