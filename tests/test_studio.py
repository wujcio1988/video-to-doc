import pytest
from pathlib import Path
from fastapi.testclient import TestClient

from vtd.studio.db import init_db, create_task, get_task, list_tasks, update_task
from vtd.studio.settings_store import load_settings, save_settings
from vtd.studio.worker import slugify, build_output_dir
from vtd.studio.app import app, resolve_video_path


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


@pytest.fixture
def recordings_dir(tmp_path, monkeypatch):
    import sys
    rec = tmp_path / "recordings"
    rec.mkdir()
    # vtd.studio.__init__ shadowuje atrybut app obiektem FastAPI — patchuj moduł przez sys.modules
    monkeypatch.setattr(sys.modules["vtd.studio.app"], "RECORDINGS_DIR", rec)
    return rec


def test_video_path_accepted_inside_recordings(recordings_dir):
    vid = recordings_dir / "nagranie.mp4"
    vid.write_bytes(b"x" * 1024)
    resolved = resolve_video_path(str(vid))
    assert resolved == vid.resolve()
    # nazwa pliku (względna) też akceptowana
    assert resolve_video_path("nagranie.mp4") == vid.resolve()


def test_video_path_traversal_rejected(recordings_dir, tmp_path):
    outside = tmp_path / "poza.mp4"
    outside.write_bytes(b"x" * 1024)
    with pytest.raises(Exception) as exc:
        resolve_video_path("../poza.mp4")
    assert exc.value.status_code == 400


def test_video_path_absolute_outside_rejected(recordings_dir):
    with pytest.raises(Exception) as exc:
        resolve_video_path("/etc/passwd")
    assert exc.value.status_code == 400


def test_video_path_symlink_escape_rejected(recordings_dir, tmp_path):
    outside = tmp_path / "ucieczka.mp4"
    outside.write_bytes(b"x" * 1024)
    link = recordings_dir / "link.mp4"
    link.symlink_to(outside)
    with pytest.raises(Exception) as exc:
        resolve_video_path(str(link))
    assert exc.value.status_code == 400


def test_video_path_missing_file_rejected(recordings_dir):
    with pytest.raises(Exception) as exc:
        resolve_video_path("nie_ma.mp4")
    assert exc.value.status_code == 400


def test_auth_required_when_token_set(client, monkeypatch):
    monkeypatch.setenv("VTD_AUTH_TOKEN", "sekret123")
    assert client.get("/").status_code == 401
    assert client.get("/tasks/1/json").status_code == 401
    assert client.get("/", headers={"X-VTD-Token": "zle"}).status_code == 401
    assert client.get("/", headers={"X-VTD-Token": "sekret123"}).status_code == 200
    # GF-014: token w query param został usunięty (wyciek do logów/referera) —
    # tylko nagłówek X-VTD-Token lub cookie vtd_token jest honorowany.
    assert client.get("/?token=sekret123").status_code == 401
    # /health bez zmian — bez nagłówka
    assert client.get("/health").status_code == 200


def test_ds010_static_exempt_when_token_set(client, monkeypatch):
    """DS-010: statyki (/static/*) dostępne bez tokenu — przeglądarka nie wysyła
    nagłówka X-VTD-Token do <link>/<img>, co łamało renderowanie UI."""
    monkeypatch.setenv("VTD_AUTH_TOKEN", "sekret123")
    assert client.get("/static/style.css").status_code == 200
    # /health nadal otwarty
    assert client.get("/health").status_code == 200


def test_ds010_login_grants_browser_cookie_session(client, monkeypatch):
    """DS-010: logowanie z przeglądarki — POST /login (token w TREŚCI formularza,
    nie URL — GF-014) ustawia cookie vtd_token, które autoryzuje kolejne żądania
    (natywne formularze i fetch działają jak dotychczas)."""
    monkeypatch.setenv("VTD_AUTH_TOKEN", "sekret123")
    res = client.post(
        "/login",
        content=b"token=sekret123",
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        follow_redirects=False,
    )
    assert res.status_code == 303
    assert res.cookies.get("vtd_token") == "sekret123"
    # cookie utrwalone w jarze klienta — kolejne żądania bez nagłówka działają
    assert client.get("/").status_code == 200


def test_ds010_login_wrong_token_rejected(client, monkeypatch):
    """DS-010: błędny token na /login → 401, brak cookie sesyjnego."""
    monkeypatch.setenv("VTD_AUTH_TOKEN", "sekret123")
    res = client.post(
        "/login",
        content=b"token=nieprawidlowy",
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    assert res.status_code == 401
    assert res.cookies.get("vtd_token") is None


def test_ds010_browser_html_navigation_redirects_to_login(client, monkeypatch):
    """DS-010: przeglądarka (Accept: text/html) bez autoryzacji dostaje redirect
    na /login, nie goły JSON 401; API klienty (Accept: */*) pozostają przy 401."""
    monkeypatch.setenv("VTD_AUTH_TOKEN", "sekret123")
    res = client.get("/", headers={"Accept": "text/html"}, follow_redirects=False)
    assert res.status_code == 303
    assert res.headers["location"] == "/login"
    assert client.get("/").status_code == 401  # API: bez nagłówka dalej 401
    assert client.get("/login").status_code == 200  # ekran logowania otwarty


def test_auth_not_required_without_token(client, monkeypatch):
    monkeypatch.delenv("VTD_AUTH_TOKEN", raising=False)
    assert client.get("/").status_code == 200
    assert client.get("/health").status_code == 200


def test_render_markdown_safe_strips_script():
    from vtd.studio.sanitize import render_markdown_safe
    out = render_markdown_safe(
        "# Tytuł\n\n<script>alert(1)</script>\n\n<img src=x onerror=alert(2)>\n\n[link](https://example.com)"
    )
    assert "<script" not in out
    assert "onerror" not in out
    assert "<img" not in out or "onerror" not in out
    assert "<h1>" in out
    assert "href=" in out


def test_worker_recovery_on_startup(tmp_path, monkeypatch):
    import vtd.studio.worker as worker_module
    data_dir = tmp_path / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr("vtd.studio.db.get_db_path", lambda: data_dir / "tasks.db")
    monkeypatch.setattr(worker_module, "_recovered", False)
    with worker_module._queue_lock:
        worker_module._queue.clear()

    init_db()
    running_id = create_task({"video_path": "/v/a.mp4", "video_name": "a.mp4", "mode": "manual"})
    update_task(running_id, status="running")
    queued_id = create_task({"video_path": "/v/b.mp4", "video_name": "b.mp4", "mode": "manual"})
    error_id = create_task({"video_path": "/v/c.mp4", "video_name": "c.mp4", "mode": "manual"})
    update_task(error_id, status="error")

    worker_module.recover_tasks_on_startup()

    assert get_task(running_id)["status"] == "error"
    assert "przerwane przez restart" in (get_task(running_id)["error_tail"] or "")
    assert get_task(queued_id)["status"] == "queued"
    assert get_task(error_id)["status"] == "error"
    with worker_module._queue_lock:
        assert queued_id in worker_module._queue
        assert running_id not in worker_module._queue
        worker_module._queue.clear()


def test_create_task_persists_output_dir(tmp_path, monkeypatch):
    data_dir = tmp_path / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr("vtd.studio.db.get_db_path", lambda: data_dir / "tasks.db")

    init_db()
    task_id = create_task({
        "video_path": "/v/a.mp4",
        "video_name": "a.mp4",
        "mode": "manual",
        "output_dir": "/out/nowy",
    })
    assert get_task(task_id)["output_dir"] == "/out/nowy"
