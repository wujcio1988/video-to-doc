"""Testy rerender_docs + regresja GF-013 (parser Dowód vs INSTRUKCJA.md)."""
import pytest

from vtd.core import rerender_docs


def _write_output(tmp_path, md_text, prompt_text=None):
    (tmp_path / "INSTRUKCJA.md").write_text(md_text, encoding="utf-8")
    if prompt_text is not None:
        (tmp_path / "PROMPT_DLA_AGENTA.txt").write_text(prompt_text, encoding="utf-8")
    (tmp_path / "metadata.json").write_text('{"title": "T", "mode": "manual"}', encoding="utf-8")
    (tmp_path / "frames").mkdir(exist_ok=True)
    (tmp_path / "frames" / "scene_0001.png").write_bytes(b"png")


MD_NO_DOWOD = """# T

### Krok 1: Zaloguj się

![Krok 1](frames/scene_0001.png)

Opis operacji.
"""

PROMPT = """Materiały wejściowe:

--- KROK 1 (Znacznik: 12.0s - 25.5s | klatka: scene_0001.png | źródło: narration/transcript) ---
Ścieżka klatki: frames/scene_0001.png
Wypowiedź lektora: Zaloguj się
Dowód: klatka=scene_0001.png, timestamp=12.0s - 25.5s
"""


def test_gf013_timestamps_from_prompt():
    out = tmp = None
    import tempfile
    from pathlib import Path
    with tempfile.TemporaryDirectory() as d:
        _write_output(Path(d), MD_NO_DOWOD, PROMPT)
        ts = rerender_docs._timestamps_from_prompt(Path(d))
    assert ts == {"scene_0001.png": (12.0, 25.5)}


def test_gf013_regression_md_parse_uses_prompt_timestamps(capsys):
    """GF-013: brak linii Dowód w md -> timestampy rekonstruowane z PROMPT, nie 0-5s."""
    import tempfile
    from pathlib import Path
    with tempfile.TemporaryDirectory() as d:
        _write_output(Path(d), MD_NO_DOWOD, PROMPT)
        steps, enriched = rerender_docs._parse_steps_from_existing_markdown(Path(d))
    assert len(steps) == 1
    assert (steps[0].start_time, steps[0].end_time) == (12.0, 25.5), \
        "timestampy muszą pochodzić z promptu, nie być sfabrykowane 0-5s"


def test_gf013_regression_missing_everywhere_warns(capsys):
    """GF-013: gdy znaczników nie ma nigdzie — jawne ostrzeżenie (fallback 0-5s pozostaje)."""
    import tempfile
    from pathlib import Path
    with tempfile.TemporaryDirectory() as d:
        _write_output(Path(d), MD_NO_DOWOD, prompt_text=None)
        steps, _ = rerender_docs._parse_steps_from_existing_markdown(Path(d))
    assert steps[0].start_time == 0.0 and steps[0].end_time == 5.0
    assert "Ostrzeżenie" in capsys.readouterr().out


def test_gf013_dowod_still_parsed_when_present():
    import tempfile
    from pathlib import Path
    md = MD_NO_DOWOD.replace(
        "Opis operacji.",
        "**Dowód:** klatka `scene_0001.png` | timestamp `3.0s - 9.0s`\n\nOpis operacji.",
    )
    with tempfile.TemporaryDirectory() as d:
        _write_output(Path(d), md, prompt_text=None)
        steps, _ = rerender_docs._parse_steps_from_existing_markdown(Path(d))
    assert (steps[0].start_time, steps[0].end_time) == (3.0, 9.0)


def test_rerender_full_flow_backup_and_rewrite(tmp_path):
    _write_output(tmp_path, MD_NO_DOWOD, PROMPT)
    (tmp_path / "client-ready").mkdir()
    result = rerender_docs.rerender_output_dir(tmp_path)
    assert result["steps"] == 1
    assert (tmp_path / "INSTRUKCJA.md").exists()
    assert any(p.name.startswith("INSTRUKCJA.md.bak") for p in tmp_path.iterdir())


def test_rerender_missing_dir_raises(tmp_path):
    with pytest.raises(FileNotFoundError):
        rerender_docs.rerender_output_dir(tmp_path / "nope")
