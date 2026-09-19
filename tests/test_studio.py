import pytest
from pathlib import Path
from fastapi.testclient import TestClient

from vtd.studio.db import init_db, create_task, get_task, list_tasks, update_task
from vtd.studio.settings_store import load_settings, save_settings
from vtd.studio.worker import slugify, build_output_dir
from vtd.studio.app import app


@pytest.fixture
def client():
    return TestClient(app)


def test_slugify():
    assert slugify("Klient Łódź 123!") == "Klient_Lodz_123"
    assert slugify("Proces: Tworzenie TH") == "Proces_Tworzenie_TH"


def test_db_tasks_crud(tmp_path, monkeypatch):
    data_dir = tmp_path / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr("vtd.studio.db.get_db_path", lambda: data_dir / "tasks.db")

    init_db()
    task_id = create_task({
        "video_path": "/path/video.mp4",
        "video_name": "video.mp4",
        "mode": "manual",
        "client": "Firma Test",
    })
    assert task_id == 1

    t = get_task(task_id)
    assert t is not None
    assert t["video_name"] == "video.mp4"
    assert t["status"] == "queued"

    update_task(task_id, status="running", stage="audio")
    t_updated = get_task(task_id)
    assert t_updated["status"] == "running"
    assert t_updated["stage"] == "audio"

    tasks = list_tasks()
    assert len(tasks) == 1


def test_settings_store(tmp_path, monkeypatch):
    data_dir = tmp_path / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr("vtd.studio.settings_store.get_settings_path", lambda: data_dir / "settings.json")

    s = load_settings()
    assert s["scene_threshold"] == 0.05
    assert s["enrich"] is False

    save_settings({"enrich": True, "scene_threshold": 0.08})
    s2 = load_settings()
    assert s2["enrich"] is True
    assert s2["scene_threshold"] == 0.08


def test_app_health_endpoint(client):
    res = client.get("/health")
    assert res.status_code == 200
    assert res.json() == {"status": "ok"}


def test_app_dashboard_endpoint(client):
    res = client.get("/")
    assert res.status_code == 200
    assert "Video-to-Doc Studio" in res.text
