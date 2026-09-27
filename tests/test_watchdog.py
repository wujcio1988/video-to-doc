"""Testy watchdog + regresje GF-003 (de-hardcodowanie) i GF-029 (wspólny loader .env)."""
import importlib
import json
import subprocess
from unittest.mock import patch

import pytest

from vtd.core import watchdog


@pytest.fixture()
def wdir(tmp_path, monkeypatch):
    """Przeładowuje watchdog z env-wskazanym katalogiem nagrań."""
    rec = tmp_path / "recordings"
    rec.mkdir()
    monkeypatch.setenv("VTD_RECORDINGS_DIR", str(rec))
    mod = importlib.reload(watchdog)
    yield mod, rec
    importlib.reload(watchdog)  # przywróć stan domyślny


def test_gf003_recordings_dir_from_env(wdir):
    mod, rec = wdir
    assert mod.RECORDINGS_DIR == rec
    # nie ma już hard-coded ścieżki autora
    assert "erp-workspace" not in str(mod.RECORDINGS_DIR)


def test_gf003_no_hardcoded_chat_id(wdir):
    """GF-003: brak HERMES_ARTIFACT_CHAT -> wysyłka pominięta z ostrzeżeniem (bez defaultu)."""
    mod, rec = wdir
    docx = rec / "INSTRUKCJA.docx"
    docx.write_bytes(b"x")
    env = {"TELEGRAM_BOT_TOKEN": "tok"}
    with patch.object(mod.subprocess, "run") as run_mock:
        mod.send_telegram_document(docx, "cap", env)
    run_mock.assert_not_called()


def test_ds002_regression_telegram_token_in_url_path(wdir):
    """DS-002 (superset GF-003): Telegram Bot API wymaga tokenu w ścieżce
    /bot<TOKEN>/sendDocument i NIE obsługuje nagłówka Authorization —
    przywrócony poprawny kontrakt URL."""
    mod, rec = wdir
    docx = rec / "INSTRUKCJA.docx"
    docx.write_bytes(b"x")
    env = {"TELEGRAM_BOT_TOKEN": "supersecret", "HERMES_ARTIFACT_CHAT": "42"}
    captured = {}

    def fake_run(cmd, **kw):
        captured["cmd"] = cmd
        return subprocess.CompletedProcess(cmd, 0, stdout='"ok":true', stderr="")

    with patch.object(mod.subprocess, "run", fake_run):
        mod.send_telegram_document(docx, "cap", env)
    cmd = captured["cmd"]
    url_args = [a for a in cmd if a.startswith("http")]
    assert url_args == ["https://api.telegram.org/botsupersecret/sendDocument"], (
        "token Bot API musi być w ścieżce URL (bez tego API zwraca 404)"
    )
    assert not any(arg.startswith("Authorization") for arg in cmd), (
        "Telegram nie honoruje nagłówka Authorization (DS-002)"
    )


def test_gf029_shared_env_loader(wdir, monkeypatch):
    """GF-029: load_env korzysta z vtd.config._load_env_file (brak duplikacji parsowania)."""
    mod, rec = wdir
    envfile = rec / "envfile"
    envfile.write_text('TELEGRAM_BOT_TOKEN="abc123"\n', encoding="utf-8")
    monkeypatch.setattr(mod, "ENV_FILE", envfile)
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    env = mod.load_env()
    assert env.get("TELEGRAM_BOT_TOKEN") == "abc123"


def test_scan_skips_already_processed(wdir, monkeypatch):
    mod, rec = wdir
    video = rec / "nagranie.mkv"
    video.write_bytes(b"v" * 2000)
    (rec / ".processed_recordings.json").write_text(
        json.dumps({"processed": {"nagranie.mkv": {"status": "success"}}}), encoding="utf-8"
    )
    assert mod.scan_and_process_once() == 0


def test_scan_processes_ready_file(wdir, monkeypatch):
    """Smoke scan: gotowy plik -> run_pipeline wywołane, stan zapisany."""
    mod, rec = wdir
    video = rec / "nagranie.mp4"
    video.write_bytes(b"v" * 2000)
    monkeypatch.setattr(mod, "is_file_ready", lambda p, min_age_seconds=8.0: True)
    called = {}

    def fake_run_pipeline(**kw):
        called["video"] = kw["video_path"]
        kw["output_dir"].mkdir(parents=True, exist_ok=True)
        (kw["output_dir"] / "INSTRUKCJA.html").write_text("html", encoding="utf-8")

    monkeypatch.setattr(mod, "run_pipeline", fake_run_pipeline)
    n = mod.scan_and_process_once(allow_telegram=False)
    assert n == 1
    assert called["video"] == video
    state = json.loads((rec / ".processed_recordings.json").read_text(encoding="utf-8"))
    assert state["processed"]["nagranie.mp4"]["status"] == "success"
