"""Testy regresyjne Runda 3 — VTD-012/014/018/021/023/027/028.

Reguła wspólna: bez sieci, bez Whisper, patch urllib/time.sleep; wyłącznie mocki
(wzorce z istniejących plików testowych: tests/test_openai_client.py,
tests/test_worker.py, tests/test_studio.py)."""
import ast
import subprocess  # noqa: F401  (wzorzec z test_worker)
import sys
import tomllib
import types
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from vtd.providers import openai_client
from vtd.studio import worker

REPO = Path(__file__).resolve().parent.parent


class _FakeProc:
    """Atrapa subprocess.Popen: proces z kodem 0 i podanym stdout."""

    def __init__(self, lines=(), returncode=0):
        self.stdout = iter(list(lines))
        self.returncode = returncode
        self.terminated = False
        self.killed = False

    def wait(self, timeout=None):
        return self.returncode

    def terminate(self):
        self.terminated = True

    def kill(self):
        self.killed = True


@pytest.fixture
def fake_db(tmp_path, monkeypatch):
    """Minimalna atrapa bazy Studio dla _execute_task (wzorzec test_worker.fake_db)."""
    tasks = {
        1: {"id": 1, "video_path": str(tmp_path / "rec" / "v.mkv"), "video_name": "v.mkv",
            "mode": "manual", "client": "ACME", "track": "auto", "scene_threshold": 0.05,
            "document_status": "DRAFT", "output_mode": "both", "max_step_seconds": 45.0,
            "grid_interval": 30.0},
    }
    updates = []

    monkeypatch.setattr(worker, "get_task", lambda tid: tasks.get(tid))
    monkeypatch.setattr(worker, "list_tasks_by_status", lambda s: [t for t in tasks.values() if t.get("status") == s])
    monkeypatch.setattr(worker, "update_task", lambda tid, **f: updates.append((tid, f)))
    monkeypatch.setattr(worker, "load_settings", lambda: {"pipeline_python": sys.executable})
    monkeypatch.setattr(worker, "load_config", lambda: types.SimpleNamespace(
        storage=types.SimpleNamespace(
            data_dir=tmp_path, output_dir=tmp_path / "out", recordings_dir=tmp_path / "rec")))
    return tasks, updates, tmp_path


# ---------------------------------------------------------------------------
# VTD-021: numpy jawne w dependencies
# ---------------------------------------------------------------------------

def test_numpy_in_project_dependencies():
    data = tomllib.loads((REPO / "pyproject.toml").read_text(encoding="utf-8"))
    deps = data["project"]["dependencies"]
    names = [d.split(">")[0].split("=")[0].split("[")[0].strip() for d in deps]
    assert "numpy" in names, f"numpy brak w dependencies: {deps}"


# ---------------------------------------------------------------------------
# VTD-018 + VTD-023: brak locals() w cli.py, stabilna sygnatura run_pipeline
# ---------------------------------------------------------------------------

def test_cli_has_no_locals_calls():
    tree = ast.parse((REPO / "vtd" / "cli.py").read_text(encoding="utf-8"))
    locals_calls = [
        n for n in ast.walk(tree)
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id == "locals"
    ]
    assert not locals_calls, f"vtd/cli.py zawiera {len(locals_calls)} wywołań locals()"


def test_run_pipeline_signature_25_params():
    from vtd.cli import run_pipeline
    count = run_pipeline.__code__.co_argcount
    names = run_pipeline.__code__.co_varnames[:count]
    assert count == 25, f"run_pipeline ma {count} parametrów: {names}"
    assert names[0] == "video_path" and names[1] == "output_dir"
    expected_tail = ("frame_qa", "min_step_seconds", "annotate", "nano_banana", "nano_mode", "nano_auto_accept")
    assert names[-6:] == expected_tail


# ---------------------------------------------------------------------------
# VTD-027: dyscyplina retry openai_client
# ---------------------------------------------------------------------------

def _provider(max_retries=3):
    return openai_client.OpenAIProvider(
        api_key="test-key", base_url="http://localhost:9999/v1", max_retries=max_retries
    )


def test_retry_skips_validation_error_no_choices():
    """ValueError 'Brak choices' — permanentny, dokładnie 1 wywołanie sieciowe."""
    fake_resp = MagicMock()
    fake_resp.__enter__ = MagicMock(return_value=MagicMock(read=lambda: b'{"choices": []}'))
    fake_resp.__exit__ = MagicMock(return_value=False)
    with patch.object(openai_client.urllib.request, "urlopen", return_value=fake_resp) as m_open, \
         patch.object(openai_client.time, "sleep") as m_sleep:
        p = _provider()
        with pytest.raises(ValueError, match="Brak choices"):
            p._execute_request({"model": "x", "messages": []})
        assert m_open.call_count == 1
        m_sleep.assert_not_called()


def test_retry_503_retries_with_exponential_sleep():
    """HTTPError 503 — max_retries+1 prób, sleep pomiędzy, backoff > 0."""
    def fake_urlopen(req, timeout=None):
        raise openai_client.urllib.error.HTTPError(req.full_url, 503, "Service Unavailable", None, None)

    sleeps = []
    with patch.object(openai_client.urllib.request, "urlopen", side_effect=fake_urlopen), \
         patch.object(openai_client.time, "sleep", side_effect=lambda s: sleeps.append(s)):
        p = _provider(max_retries=3)
        with pytest.raises(RuntimeError, match="503"):
            p._execute_request({"model": "x", "messages": []})
    assert len(sleeps) == 3 and all(s > 0 for s in sleeps)
    # wykładniczo rosnący (jitter ±25% nie zmienia trendu przy bazie 1,2,4)
    assert sleeps[1] > sleeps[0] * 1.3 and sleeps[2] > sleeps[1] * 1.3


# ---------------------------------------------------------------------------
# VTD-012: marker postępu %%VTD_PROGRESS w workerze
# ---------------------------------------------------------------------------

def test_parse_progress_marker_variants():
    assert worker._parse_progress_marker(
        '##VTD_PROGRESS:{"stage": "transcription", "note": "notka"}'
    ) == {"stage": "transcription", "note": "notka"}
    assert worker._parse_progress_marker("  ##VTD_PROGRESS:{\"stage\":\"scenes\"}  ") == {"stage": "scenes"}
    assert worker._parse_progress_marker("##VTD_PROGRESS:{złe json") is None
    assert worker._parse_progress_marker("##VTD_PROGRESS:[1,2]") is None
    assert worker._parse_progress_marker("##VTD_PROGRESS:") is None
    assert worker._parse_progress_marker("Krok 1/6: Ekstrakcja toru audio...") is None


def test_execute_task_marker_and_fallback_updates_stage(fake_db, monkeypatch):
    """Marker aktualizuje stage/progress_note; fallback 'Krok N/6' nadal działa."""
    tasks, updates, tmp = fake_db

    def fake_popen(cmd, **kw):
        return _FakeProc(lines=(
            '##VTD_PROGRESS:{"stage": "scenes", "note": "note-marcera"}\n',
            "Krok 5/6: Deduplikacja percepcyjna pHash...\n",
            "Krok 6/6: Fuzja sygnałów i generowanie dokumentacji...\n",
        ))

    monkeypatch.setattr(worker.subprocess, "Popen", fake_popen)
    worker._execute_task(1)

    stage_notes = {u["stage"]: u.get("progress_note") for _, u in updates if isinstance(u, dict) and "stage" in u}
    assert stage_notes.get("scenes") == "note-marcera", f"marker: {stage_notes}"
    assert stage_notes.get("dedup"), "fallback Krok 5/6 -> stage dedup"
    assert stage_notes.get("fusion"), "fallback Krok 6/6 -> stage fusion"


def test_stage_notes_mapping_intact():
    m = worker._STAGE_NOTES
    assert m["audio"] == "Ekstrakcja toru audio"
    assert m["silence"] == "Wykrywanie pauz w mowie"
    assert m["transcription"] == "Transkrypcja mowy (Whisper)"
    assert m["scenes"] == "Detekcja scen i wycinanie klatek"
    assert m["dedup"] == "Deduplikacja percepcyjna pHash"
    assert m["fusion"] == "Fuzja sygnałów i generowanie dokumentacji"
    assert m["frame_qa"] == "Weryfikacja czytelności klatek"
    assert m["annotations"] == "Generowanie adnotacji wizualnych"


# ---------------------------------------------------------------------------
# VTD-014: WAL + busy_timeout + init raz
# ---------------------------------------------------------------------------

def test_db_connection_wal_busy_timeout(tmp_path, monkeypatch):
    from vtd.studio import db

    monkeypatch.setattr(db, "get_db_path", lambda: tmp_path / "reg.db")
    db._initialized_paths.clear()
    with db.get_connection() as conn:
        assert conn.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
        assert conn.execute("PRAGMA busy_timeout").fetchone()[0] == 5000
        assert conn.execute("PRAGMA synchronous").fetchone()[0] == 1  # NORMAL


def test_db_init_paths_guard_and_wal_table(tmp_path, monkeypatch):
    """init_db per-path: sciezka wchodzi do _initialized_paths, CREATE TABLE raz,
    tabela tasks istnieje, WAL trwa przy kolejnych polaczeniach."""
    from vtd.studio import db

    db_path = tmp_path / "once.db"
    monkeypatch.setattr(db, "get_db_path", lambda: db_path)
    db._initialized_paths.clear()
    db.init_db()
    db.init_db()  # drugi raz: no-op (per-path) — nie może rzucić ani zdublować
    assert str(db_path) in {str(p) for p in db._initialized_paths}
    with db.get_connection() as conn:
        assert conn.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
        row = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='tasks'"
        ).fetchone()
        assert row is not None, "tabela tasks istnieje"
        n_tables = conn.execute(
            "SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name='tasks'"
        ).fetchone()[0]
        assert n_tables == 1, "CREATE TABLE wykonane dokładnie raz (bez duplikatów)"


# ---------------------------------------------------------------------------
# VTD-028: SSRF walidacja + klucz w nagłówku zamiast query
# ---------------------------------------------------------------------------

def test_validate_fetch_url_blocks_private_and_metadata():
    from vtd.core.image_enhancer import EnhanceError, _validate_fetch_url
    for bad in (
        "https://169.254.169.254/latest/meta-data/",
        "https://169.254.10.1/x",
        "http://127.0.0.1/x",
        "https://10.1.2.3/x",
        "https://172.16.0.9/x",
        "https://192.168.1.1/x",
        "https://[::1]/x",
        "https://[fe80::1]/x",
        "https://[fc00::1]/x",
        "file:///etc/passwd",
        "ftp://example.com/x",
    ):
        with pytest.raises(EnhanceError):
            _validate_fetch_url(bad)


def test_validate_fetch_url_allows_public_ip_literal():
    from vtd.core.image_enhancer import _validate_fetch_url
    _validate_fetch_url("https://8.8.8.8/img.png")


def test_omniroute_image_call_rejects_ssrf_without_fetch(tmp_path):
    from vtd.core import image_enhancer

    # realny mały PNG (64x64) — read_bytes potrzebne, by dojść do walidacji URL-a
    png = tmp_path / "frame.png"
    (png.write_bytes(
        b"\x89PNG\r\n\x1a\n"  # sygnatura; treść nieistotna — brak dekodowania w tej ścieżce
        b"\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x06\x00\x00\x00\x1f\x15\xc4\x89"
    ))

    # odpowiedź gatewaya z niebezpiecznym URL: mockujemy wywołanie sieciowe odpowiadające JSON-em
    class _CM:
        def __enter__(self):
            return MagicMock(read=lambda: b'{"data": [{"url": "https://169.254.169.254/meta"}]}')

        def __exit__(self, *a):
            return False

    with patch.object(image_enhancer, "_urlopen_with_retry", return_value=_CM()) as m_retry:
        with pytest.raises(image_enhancer.EnhanceError, match="VTD-028"):
            image_enhancer._omniroute_image_call(png, "prompt", "key", model="m")
    # raz dla odpowiedzi gatewaya; NIE dla pobrania wyniku z odrzuconego URL
    assert m_retry.call_count == 1


def test_gemini_text_call_key_in_header_not_url():
    from vtd.core import image_enhancer
    captured = {}

    class _CM:
        def __enter__(self):
            return MagicMock(read=lambda: b'{"usageMetadata": {}}')

        def __exit__(self, *a):
            return False

    def fake_urlopen(req, timeout=None):
        captured["req"] = req
        return _CM()

    with patch.object(image_enhancer, "_urlopen_with_retry", side_effect=fake_urlopen):
        image_enhancer._gemini_text_call([{"text": "hi"}], api_key="secret-key", model="m")

    req = captured["req"]
    assert "key=" not in req.full_url, "klucz nie może trafić do URL"
    assert req.get_header("X-goog-api-key") == "secret-key"
