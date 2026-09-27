"""Testy workera Studio — flow subprocess z atrapami (bez realnych procesów)."""
import subprocess
import sys
import types
from unittest.mock import patch

import pytest

from vtd.studio import worker


class _FakeProc:
    """Atrapa subprocess.Popen: gotowy proces z kodem 0 i strumieniem stdout."""

    def __init__(self, lines=("Krok 1/6: Ekstrakcja toru audio...\n",), returncode=0, hang=False):
        self.stdout = iter(list(lines))
        self.returncode = returncode
        self.hang = hang
        self.terminated = False
        self.killed = False

    def wait(self, timeout=None):
        if self.hang:
            raise subprocess.TimeoutExpired("vtd", timeout)
        return self.returncode

    def terminate(self):
        self.terminated = True

    def kill(self):
        self.killed = True


@pytest.fixture()
def fake_db(monkeypatch, tmp_path):
    """Atrapa DB: get_task/update_task/list_tasks_by_status w pamięci."""
    tasks = {
        1: {"id": 1, "video_path": "/recs/v.mkv", "video_name": "v.mkv", "mode": "manual",
            "client": "ACME", "track": "auto", "scene_threshold": 0.05,
            "document_status": "DRAFT", "output_mode": "both", "max_step_seconds": 45.0,
            "grid_interval": 30.0},
    }
    updates = []

    def get_task(tid):
        return tasks.get(tid)

    def update_task(tid, **fields):
        updates.append((tid, fields))
        tasks[tid].update(fields)

    def list_by_status(status):
        return [t for t in tasks.values() if t.get("status") == status]

    monkeypatch.setattr(worker, "get_task", get_task)
    monkeypatch.setattr(worker, "update_task", update_task)
    monkeypatch.setattr(worker, "list_tasks_by_status", list_by_status)
    monkeypatch.setattr(worker, "load_settings", lambda: {"pipeline_python": sys.executable})
    cfg = types.SimpleNamespace(storage=types.SimpleNamespace(
        data_dir=tmp_path, output_dir=tmp_path / "out", recordings_dir=tmp_path / "rec"))
    monkeypatch.setattr(worker, "load_config", lambda: cfg)
    return tasks, updates, tmp_path


def test_slugify():
    assert worker.slugify("Klient Łódź/Sp. z o.o.") == "Klient_Lodz_Sp_z_o_o"
    assert worker.slugify("   ") == "unknown"


def test_execute_task_success_flow(fake_db, monkeypatch):
    tasks, updates, tmp = fake_db
    procs = []

    def fake_popen(cmd, **kw):
        procs.append(cmd)
        return _FakeProc()

    monkeypatch.setattr(worker.subprocess, "Popen", fake_popen)
    worker._execute_task(1)
    assert procs, "subprocess musi zostać uruchomiony"
    cmd = procs[0]
    assert cmd[:3] == [sys.executable, "-m", "vtd", "run"][:3]
    assert "--client" in cmd and "ACME" in cmd
    final = tasks[1]
    assert final["status"] == "done"
    assert final["stage"] == "complete"
    assert (tmp / "task_1.log").exists(), "log zadania zapisany"


def test_execute_task_failure_captures_tail(fake_db, monkeypatch):
    tasks, updates, tmp = fake_db

    def fake_popen(cmd, **kw):
        return _FakeProc(lines=("BŁĄD: coś pękło\n",), returncode=2)

    monkeypatch.setattr(worker.subprocess, "Popen", fake_popen)
    worker._execute_task(1)
    assert tasks[1]["status"] == "error"
    assert "coś pękło" in tasks[1]["error_tail"]


def test_execute_task_timeout_terminates(fake_db, monkeypatch):
    """VTD-004: przekroczenie limitu -> terminate/kill + status error."""
    tasks, updates, tmp = fake_db
    holder = {}

    def fake_popen(cmd, **kw):
        p = _FakeProc(hang=True)
        holder["proc"] = p
        return p

    monkeypatch.setattr(worker.subprocess, "Popen", fake_popen)
    monkeypatch.setattr(worker, "TASK_TIMEOUT_SECONDS", 0.05)
    monkeypatch.setattr(worker, "TERMINATE_GRACE_SECONDS", 0.05)
    worker._execute_task(1)
    p = holder["proc"]
    assert p.terminated or p.killed
    assert tasks[1]["status"] == "error"
    assert "limit czasu" in tasks[1]["error_tail"]


def test_execute_task_missing_task_noop(fake_db, monkeypatch):
    _, updates, _ = fake_db
    worker._execute_task(999)
    assert updates == []


def test_recovery_marks_running_as_error_and_requeues(fake_db, monkeypatch):
    tasks, updates, _ = fake_db
    tasks[2] = {"id": 2, "status": "running"}
    tasks[3] = {"id": 3, "status": "queued"}
    enqueued = []
    monkeypatch.setattr(worker, "enqueue_task", lambda tid: enqueued.append(tid))
    worker._recovered = False
    worker.recover_tasks_on_startup()
    assert tasks[2]["status"] == "error"
    assert "restart" in tasks[2]["error_tail"]
    assert enqueued == [3]
    # idempotencja
    worker.recover_tasks_on_startup()
    assert enqueued == [3]


def test_terminate_process_grace_then_kill():
    """VTD-004: terminate, po grace period kill."""
    calls = []

    class _Stubborn(_FakeProc):
        def wait(self, timeout=None):
            calls.append(timeout)
            raise subprocess.TimeoutExpired("x", timeout)

        def kill(self):
            calls.append("kill")

    p = _Stubborn()
    worker._terminate_process(p)
    assert "kill" in calls
