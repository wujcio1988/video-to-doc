"""Testy rerender_docs + regresja GF-013 (parser Dowód vs INSTRUKCJA.md)."""
import json

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


# --- R1: steps.json jako kanoniczne źródło ---

STEPS_JSON = {
    "version": "1.0",
    "steps": [
        {
            "step_number": 1,
            "start_time": 3.0,
            "end_time": 9.0,
            "frame_path": "frames/scene_0001.png",
            "speech_text": "Mowa z JSON.",
            "grid_frames": [],
            "annotations": [],
        },
        {
            "step_number": 2,
            "start_time": 9.0,
            "end_time": 17.0,
            "frame_path": "frames/scene_0002.png",
            "speech_text": "Druga mowa z JSON.",
            "grid_frames": [],
            "annotations": [],
        },
    ],
    "enriched_data": {
        "meta": {"document_status": "DRAFT", "mode": "manual"},
        "intro": "Intro z JSON.",
        "steps": [
            {"step_number": 1, "title": "Tytuł kroku z JSON", "description": "Opis z JSON."},
            {"step_number": 2, "title": "Drugi tytuł z JSON", "description": "Drugi opis z JSON."},
        ],
    },
    "annotation_legend": None,
}


def _write_steps_json(tmp_path, payload=STEPS_JSON):
    (tmp_path / "steps.json").write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


def _spy_builders(monkeypatch):
    """Spy na buildery — przechwytuje kwargs, zapisuje pliki tam, gdzie rerender je czyta."""
    calls = {}

    def spy_md(**kwargs):
        calls["md"] = kwargs
        titles = [
            s.get("title", "") for s in (kwargs.get("enriched_data") or {}).get("steps", [])
        ]
        return "# " + kwargs["title"] + "\n" + "\n".join(titles) + "\n"

    def spy_html(**kwargs):
        calls["html"] = kwargs
        kwargs["output_html"].write_text("<html>ok</html>", encoding="utf-8")
        return kwargs["output_html"]

    def spy_docx(**kwargs):
        calls["docx"] = kwargs
        kwargs["output_docx"].write_bytes(b"docx")
        return kwargs["output_docx"]

    monkeypatch.setattr(rerender_docs, "render_markdown_manual", spy_md)
    monkeypatch.setattr(rerender_docs, "render_html_manual", spy_html)
    monkeypatch.setattr(rerender_docs, "render_docx_manual", spy_docx)
    return calls


# T3 — gdy steps.json istnieje, rerender go używa (a nie parsuje MD/PROMPT)
def test_rerender_uses_steps_json_when_present(tmp_path, monkeypatch):
    # pułapka: MD i PROMPT mówią coś zupełnie innego niż steps.json
    (tmp_path / "INSTRUKCJA.md").write_text(
        MD_NO_DOWOD.replace("Zaloguj się", "TYTUŁ Z MD — PUŁAPKA"), encoding="utf-8"
    )
    (tmp_path / "PROMPT_DLA_AGENTA.txt").write_text("", encoding="utf-8")
    (tmp_path / "metadata.json").write_text('{"title": "T", "mode": "manual"}', encoding="utf-8")
    (tmp_path / "frames").mkdir()
    (tmp_path / "frames" / "scene_0001.png").write_bytes(b"png")
    (tmp_path / "frames" / "scene_0002.png").write_bytes(b"png")
    _write_steps_json(tmp_path)

    calls = _spy_builders(monkeypatch)
    result = rerender_docs.rerender_output_dir(tmp_path)

    assert result["steps"] == 2
    # kroki pochodzą z JSON: liczby i timestampy, nie z MD/PROMPT
    md_steps = calls["md"]["steps"]
    assert [s.step_number for s in md_steps] == [1, 2]
    assert (md_steps[0].start_time, md_steps[0].end_time) == (3.0, 9.0)
    assert md_steps[0].speech_text == "Mowa z JSON."
    # frame_path odbudowane absolutnie względem output_dir
    assert md_steps[0].frame_path == tmp_path / "frames" / "scene_0001.png"
    # enriched z JSON, nie z MD (pułapka "TYTUŁ Z MD" nie występuje)
    assert calls["md"]["enriched_data"]["steps"][0]["title"] == "Tytuł kroku z JSON"
    md_text = (tmp_path / "INSTRUKCJA.md").read_text(encoding="utf-8")
    assert "Tytuł kroku z JSON" in md_text
    assert "TYTUŁ Z MD — PUŁAPKA" not in md_text


# T4 — bez steps.json: backward compat, rekonstrukcja z PROMPT
def test_rerender_fallback_without_steps_json(tmp_path, monkeypatch):
    _write_output(tmp_path, MD_NO_DOWOD, PROMPT)
    calls = _spy_builders(monkeypatch)

    real_ts = rerender_docs._timestamps_from_prompt
    ts_calls = {"n": 0}

    def counting_ts(output_dir):
        ts_calls["n"] += 1
        return real_ts(output_dir)

    monkeypatch.setattr(rerender_docs, "_timestamps_from_prompt", counting_ts)

    result = rerender_docs.rerender_output_dir(tmp_path)
    assert result["steps"] == 1
    assert ts_calls["n"] >= 1, "backward compat: parser PROMPT musi być użyty bez steps.json"
    assert (calls["md"]["steps"][0].start_time, calls["md"]["steps"][0].end_time) == (12.0, 25.5)
    assert (tmp_path / "INSTRUKCJA.md").exists()


# --- R3: DOCX w rerender ---

# T5 — rerender generuje INSTRUKCJA.docx (dokładnie raz) + kopia do client-ready
def test_rerender_generates_docx(tmp_path, monkeypatch):
    _write_output(tmp_path, MD_NO_DOWOD, PROMPT)
    (tmp_path / "client-ready").mkdir()
    _spy_builders(monkeypatch)

    spy_docx = rerender_docs.render_docx_manual
    counter = {"n": 0}

    def counting_docx(**kwargs):
        counter["n"] += 1
        return spy_docx(**kwargs)

    monkeypatch.setattr(rerender_docs, "render_docx_manual", counting_docx)

    result = rerender_docs.rerender_output_dir(tmp_path)
    assert counter["n"] == 1, "render_docx_manual musi być wywołany dokładnie raz"
    assert "docx" in result
    assert result["docx"] == str(tmp_path / "INSTRUKCJA.docx")
    assert (tmp_path / "INSTRUKCJA.docx").exists()
    assert (tmp_path / "client-ready" / "INSTRUKCJA.docx").exists()


# T6 — istniejący INSTRUKCJA.docx dostaje backup .bak.<ts>.docx przed nadpisaniem
def test_rerender_docx_backup_created_if_exists(tmp_path, monkeypatch):
    _write_output(tmp_path, MD_NO_DOWOD, PROMPT)
    (tmp_path / "INSTRUKCJA.docx").write_bytes(b"stary-docx")
    _spy_builders(monkeypatch)

    rerender_docs.rerender_output_dir(tmp_path)

    baks = list(tmp_path.glob("INSTRUKCJA.bak.*.docx"))
    assert len(baks) == 1, f"oczekiwano 1 backupu .bak.<ts>.docx, jest: {[p.name for p in baks]}"
    assert baks[0].read_bytes() == b"stary-docx"
    assert (tmp_path / "INSTRUKCJA.docx").read_bytes() == b"docx", "nowy DOCX nadpisuje stary"
