from fastapi import FastAPI, Request, Form, HTTPException, Query
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, PlainTextResponse, StreamingResponse
from urllib.parse import parse_qs
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pathlib import Path
from typing import Optional
import html
import json
import logging
import os
import zipfile
import io
from datetime import datetime

from contextlib import asynccontextmanager

from vtd.studio.db import create_task, get_task, list_tasks
from vtd.studio.settings_store import load_settings, save_settings, ALLOWED_MODELS
from vtd.defaults import DEFAULT_ENRICH_MODEL
from vtd.studio.sanitize import render_markdown_safe
from vtd.studio import worker

logger = logging.getLogger("vtd.studio")

BASE_DIR = Path(__file__).parent
TEMPLATES_DIR = BASE_DIR / "templates"
STATIC_DIR = BASE_DIR / "static"
from vtd.config import load_config
RECORDINGS_DIR = load_config().storage.recordings_dir

templates = Jinja2Templates(directory=str(TEMPLATES_DIR))

AUTH_TOKEN_ENV = "VTD_AUTH_TOKEN"
# DS-010: /health (liveness), /login (ekran logowania dający cookie), /static/<plik>
# (statyki niewrażliwe: style.css, app.js — przeglądarka nie wysyła nagłówka
# X-VTD-Token do <link>/<img>, co łamało renderowanie UI przy włączonym tokenie).
AUTH_EXEMPT_PATHS = {"/health", "/login"}
AUTH_STATIC_PREFIX = "/static/"
AUTH_COOKIE_NAME = "vtd_token"  # DS-010: sesja przeglądarki (HttpOnly, SameSite=Strict)


def get_auth_token() -> str:
    return os.environ.get(AUTH_TOKEN_ENV, "").strip()


# GF-014: porównanie stało-czasowe (timing side-channel)
def _token_matches(provided: str, expected: str) -> bool:
    import hmac
    return hmac.compare_digest(provided.encode("utf-8"), expected.encode("utf-8"))


@asynccontextmanager
async def lifespan(application: FastAPI):
    # GF-015: migracja z deprecated @app.on_event("startup") na lifespan handler
    if not get_auth_token():
        logger.warning(
            "VTD_AUTH_TOKEN nie jest ustawiony — Studio działa bez autoryzacji; "
            "przy starcie przez CLI binduje 127.0.0.1. Ustaw VTD_AUTH_TOKEN dla dostępu sieciowego."
        )
    worker.start_worker()
    yield


app = FastAPI(title="Video-to-Doc Studio", lifespan=lifespan)


@app.middleware("http")
async def require_auth_token(request: Request, call_next):
    token = get_auth_token()
    path = request.url.path
    if not token or path in AUTH_EXEMPT_PATHS or path.startswith(AUTH_STATIC_PREFIX):
        return await call_next(request)
    # GF-014: token w query param pozostaje ODRZUCONY (wyciek do logów serwera
    # i historii przeglądarki); porównanie stało-czasowe.
    # DS-010: przeglądarka nie wysyła nagłówka X-VTD-Token ani do natywnych submitów
    # formularzy, ani do fetch() — akceptujemy też cookie vtd_token (HttpOnly,
    # SameSite=Strict blokuje cross-site), którego nadaje POST /login z poprawnym tokenem.
    provided = request.headers.get("X-VTD-Token") or request.cookies.get(AUTH_COOKIE_NAME) or ""
    if not _token_matches(provided, token):
        # Przeglądarkowa nawigacja dostaje ekran logowania zamiast gołego JSON 401;
        # API klienci (Accept: */*) dostają deterministyczne 401 jak dotychczas.
        if request.method == "GET" and "text/html" in request.headers.get("accept", "") \
                and not path.startswith("/api"):
            return RedirectResponse(url="/login", status_code=303)
        return JSONResponse(
            {"detail": "Brak autoryzacji: podaj poprawny nagłówek X-VTD-Token lub zaloguj się na /login"},
            status_code=401,
        )
    return await call_next(request)


def _render_login_page(request: Request, error: bool, status_code: int = 200):
    return templates.TemplateResponse(
        request, "login.html", {"error": error}, status_code=status_code
    )


@app.get("/login", response_class=HTMLResponse)
async def login_page(request: Request):
    if not get_auth_token():
        # tryb bez auth — ekran logowania niepotrzebny, prosto na dashboard
        return RedirectResponse(url="/", status_code=303)
    return _render_login_page(request, error=False)


@app.post("/login")
async def login_submit(request: Request):
    """DS-010: logowanie dla przeglądarki — token w TREŚCI formularza (POST body),
    nie w URL (GF-014 pozostaje niezmieniony). Ustala HttpOnly cookie używane przez
    natywne formularze i fetch() jako poświadczenie sesji."""
    expected = get_auth_token()
    if not expected:
        return RedirectResponse(url="/", status_code=303)
    body = (await request.body()).decode("utf-8", errors="replace")
    fields = parse_qs(body)
    provided = (fields.get("token") or [""])[0].strip()
    if not provided or not _token_matches(provided, expected):
        return _render_login_page(request, error=True, status_code=401)
    response = RedirectResponse(url="/", status_code=303)
    response.set_cookie(
        AUTH_COOKIE_NAME,
        value=provided,
        httponly=True,
        samesite="strict",
        path="/",
    )
    return response


def resolve_video_path(video: str) -> Path:
    """Waliduje ścieżkę pliku wideo: musi istnieć i leżeć wewnątrz RECORDINGS_DIR.

    Odrzuca traversal (`..`), symlinki wychodzące poza katalog nagrań oraz
    ścieżki absolutne spoza RECORDINGS_DIR. Rzuca HTTPException(400) przy odrzuceniu.
    """
    raw = (video or "").strip()
    if not raw:
        raise HTTPException(status_code=400, detail="Nie podano ścieżki pliku wideo")
    root = RECORDINGS_DIR.resolve()
    candidate = Path(raw)
    if not candidate.is_absolute():
        rel = RECORDINGS_DIR / raw
        if rel.is_file():
            candidate = rel
        else:
            candidate = RECORDINGS_DIR / candidate.name
    try:
        resolved = candidate.resolve()
    except Exception:
        raise HTTPException(status_code=400, detail=f"Nieprawidłowa ścieżka pliku wideo: {raw}")
    if not resolved.is_relative_to(root):
        raise HTTPException(
            status_code=400,
            detail=f"Ścieżka pliku wideo musi znajdować się w katalogu nagrań ({root}): {raw}",
        )
    if not resolved.is_file():
        raise HTTPException(status_code=400, detail=f"Plik video nie istnieje: {raw}")
    return resolved

# mount static if exists
if STATIC_DIR.exists():
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


def list_recordings():
    files = []
    if not RECORDINGS_DIR.exists():
        return files
    for p in sorted(RECORDINGS_DIR.iterdir(), key=lambda x: x.stat().st_mtime if x.is_file() else 0, reverse=True):
        if p.is_file() and p.suffix.lower() in (".mkv", ".mp4", ".avi", ".mov", ".wmv"):
            try:
                st = p.stat()
                files.append({
                    "name": p.name,
                    "path": str(p),
                    "size": st.st_size,
                    "size_mb": round(st.st_size / (1024*1024), 2),
                    "mtime": datetime.fromtimestamp(st.st_mtime).strftime("%Y-%m-%d %H:%M"),
                    "mtime_ts": st.st_mtime,
                })
            except Exception:
                continue
    return files


def is_video_processed(video_path: str, tasks: list) -> bool:
    for t in tasks:
        if t.get("video_path") == video_path and t.get("status") in ("done", "running", "queued"):
            return True
    return False


@app.get("/health")
async def health():
    return {"status": "ok"}


@app.get("/", response_class=HTMLResponse)
async def dashboard(request: Request):
    recordings = list_recordings()
    tasks = list_tasks(limit=100)
    # mark processed
    for r in recordings:
        r["processed"] = is_video_processed(r["path"], tasks)
    return templates.TemplateResponse(request, "index.html", {
        "recordings": recordings,
        "tasks": tasks,
    })


@app.get("/new", response_class=HTMLResponse)
async def new_task_form(request: Request, video: str = Query(default="")):
    settings = load_settings()
    # if video param given, try to validate exists
    video_exists = False
    if video:
        try:
            video = str(resolve_video_path(video))
            video_exists = True
        except HTTPException:
            video_exists = False
    # auto-detekcja toru (limit 3s, nie blokuj UI)
    track_detect = None
    track_detect_error = None
    if video_exists and video:
        try:
            import concurrent.futures as _cf
            from vtd.core.audio_detection import detect_best_track as _detect
            with _cf.ThreadPoolExecutor(max_workers=1) as ex:
                fut = ex.submit(_detect, video)
                try:
                    track_detect = fut.result(timeout=3)
                except _cf.TimeoutError:
                    track_detect = None
                    track_detect_error = "detekcja w tle przy uruchomieniu"
        except Exception as e:
            track_detect_error = str(e)[:200]
            track_detect = None
    recordings = list_recordings()
    return templates.TemplateResponse(request, "new.html", {
        "video": video,
        "video_exists": video_exists,
        "recordings": recordings,
        "settings": settings,
        "models": ALLOWED_MODELS,
        "track_detect": track_detect,
        "track_detect_error": track_detect_error,
    })


@app.post("/tasks")
async def create_task_endpoint(
    request: Request,
    video_path: str = Form(...),
    mode: str = Form(...),
    client: str = Form(default=""),
    process: str = Form(default=""),
    module: str = Form(default=""),
    environment: str = Form(default=""),
    author: str = Form(default=""),
    scene_threshold: str = Form(default="0.05"),
    track: str = Form(default="auto"),
    enrich: str = Form(default=""),
    enrich_model: Optional[str] = Form(default=None),
    frame_qa: str = Form(default=""),
    annotate: str = Form(default=""),
    nano_banana: str = Form(default=""),
):
    # Validation
    mode = mode.strip()
    if mode not in ("manual", "meeting"):
        raise HTTPException(status_code=400, detail="Nieprawidłowy tryb (manual|meeting)")
    client = client.strip()
    if mode == "manual" and not client:
        raise HTTPException(status_code=400, detail="Klient wymagany dla trybu manual (instrukcja)")
    # video must exist and be inside RECORDINGS_DIR (anti-traversal)
    vp = resolve_video_path(video_path)

    try:
        thr = float(scene_threshold)
    except Exception:
        thr = 0.05
    if thr <= 0 or thr >= 1:
        thr = 0.05

    if track not in ("mic", "mix", "meeting", "0", "1", "2", "auto"):
        track = "auto"

    enrich_bool = bool(enrich and enrich not in ("0", "false", "off"))
    frame_qa_bool = bool(frame_qa and frame_qa not in ("0", "false", "off"))
    annotate_bool = bool(annotate and annotate not in ("0", "false", "off"))
    nano_banana_bool = bool(nano_banana and nano_banana not in ("0", "false", "off"))
    # GF-016: walidacja modelu względem ALLOWED_MODELS, fallback na wspólny default
    enrich_model = (enrich_model or "").strip() or DEFAULT_ENRICH_MODEL
    if enrich_model not in ALLOWED_MODELS:
        enrich_model = DEFAULT_ENRICH_MODEL
    # meeting mode disables annotate and nano_banana
    if mode == "meeting":
        annotate_bool = False
        nano_banana_bool = False
    # default from settings if not explicitly checked? checkbox off means empty string -> False, but settings default True means checked by default
    # For manual mode, if annotate not provided and settings annotate True, use settings. The form will send checked value when checked.
    # To respect settings default, if annotate field missing entirely, fall back to settings.
    # But FastAPI always provides default "" -> we already handled. If user unchecked, it will be False.
    if enrich_model not in ALLOWED_MODELS:
        # GF-016: fallback na wspólny default (wcześniej "artur-daily" — model
        # NIEBĘDĄCY na liście ALLOWED_MODELS, walidacja niczego nie chroniła)
        enrich_model = DEFAULT_ENRICH_MODEL

    video_name = vp.name
    # output_dir i log_path wylicza TYLKO worker (VTD-006) — bez podwójnego tworzenia katalogów
    settings = load_settings()
    # if annotate not explicitly sent (form checkbox unchecked = ""), respect? Already computed annotate_bool; but if settings annotate True and mode manual and user didn't touch, form sends "1" when checked. So ok.
    task_id = create_task({
        "video_path": str(vp),
        "video_name": video_name,
        "mode": mode,
        "client": client if client else None,
        "process": process.strip() or None,
        "module": module.strip() or None,
        "environment": environment.strip() or None,
        "author": author.strip() or None,
        "document_status": "DRAFT",
        "output_mode": "both",
        "scene_threshold": thr,
        "track": track,
        "enrich": enrich_bool,
        "enrich_model": enrich_model if enrich_bool else None,
        "frame_qa": frame_qa_bool,
        "max_step_seconds": settings.get("max_step_seconds", 45.0),
        "grid_interval": settings.get("grid_interval", 30.0),
        "annotate": annotate_bool,
        "nano_banana": nano_banana_bool,
    })

    # enqueue
    worker.enqueue_task(task_id)

    # Redirect to task detail
    return RedirectResponse(url=f"/tasks/{task_id}", status_code=303)


@app.get("/tasks/{task_id}", response_class=HTMLResponse)
async def task_detail(request: Request, task_id: int):
    task = get_task(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Zadanie nie znalezione")
    # JSON endpoint for polling if requested via accept header? We'll support ?format=json
    if request.query_params.get("format") == "json":
        return JSONResponse(task)
    return templates.TemplateResponse(request, "task_detail.html", {
        "task": task,
    })


@app.get("/tasks/{task_id}/json")
async def task_json(task_id: int):
    task = get_task(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Zadanie nie znalezione")
    return JSONResponse(task)


@app.get("/tasks/{task_id}/log")
async def task_log(task_id: int):
    task = get_task(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Zadanie nie znalezione")
    log_path = task.get("log_path")
    if not log_path or not Path(log_path).exists():
        return PlainTextResponse("Brak logu (jeszcze nie uruchomiono lub plik nie istnieje).", status_code=404)
    content = Path(log_path).read_text(encoding="utf-8", errors="ignore")
    return PlainTextResponse(content, media_type="text/plain; charset=utf-8")


@app.get("/tasks/{task_id}/results", response_class=HTMLResponse)
async def task_results(request: Request, task_id: int):
    task = get_task(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Zadanie nie znalezione")
    output_dir = Path(task["output_dir"]) if task.get("output_dir") else None
    files_info = []
    instrukcja_html = ""
    transkrypcja_html = ""
    completeness_html = ""
    metadata_json = None
    manifest_json = None
    client_ready_files = []

    if output_dir and output_dir.exists():
        def load_md_as_html(path: Path):
            if path.exists():
                text = path.read_text(encoding="utf-8", errors="ignore")
                try:
                    return render_markdown_safe(text), text
                except Exception:
                    return f"<pre>{html.escape(text)}</pre>", text
            return "", ""

        instr_path = output_dir / "INSTRUKCJA.md"
        trans_path = output_dir / "TRANSKRYPCJA.md"
        comp_path = output_dir / "COMPLETENESS_REPORT.md"
        meta_path = output_dir / "metadata.json"
        manifest_path = output_dir / "manifest.json"

        instrukcja_html, _ = load_md_as_html(instr_path)
        transkrypcja_html, _ = load_md_as_html(trans_path)
        completeness_html, _ = load_md_as_html(comp_path)

        if meta_path.exists():
            try:
                metadata_json = json.loads(meta_path.read_text(encoding="utf-8"))
            except Exception:
                metadata_json = None
        if manifest_path.exists():
            try:
                manifest_json = json.loads(manifest_path.read_text(encoding="utf-8"))
            except Exception:
                manifest_json = None

        client_ready_dir = output_dir / "client-ready"
        if client_ready_dir.exists():
            for f in sorted(client_ready_dir.rglob("*")):
                if f.is_file():
                    rel = f.relative_to(client_ready_dir)
                    try:
                        size = f.stat().st_size
                    except Exception:
                        size = 0
                    client_ready_files.append({
                        "name": str(rel),
                        "size": size,
                        "size_kb": round(size/1024, 2),
                        "path": str(f),
                    })
        # Also list main dir files for info
        for fname in ["INSTRUKCJA.md", "INSTRUKCJA.docx", "INSTRUKCJA.html", "TRANSKRYPCJA.md", "metadata.json", "COMPLETENESS_REPORT.md", "manifest.json"]:
            p = output_dir / fname
            if p.exists():
                try:
                    size = p.stat().st_size
                except Exception:
                    size = 0
                files_info.append({"name": fname, "size": size})

    return templates.TemplateResponse(request, "results.html", {
        "task": task,
        "output_dir": str(output_dir) if output_dir else "",
        "instrukcja_html": instrukcja_html,
        "transkrypcja_html": transkrypcja_html,
        "completeness_html": completeness_html,
        "metadata_json": metadata_json,
        "manifest_json": manifest_json,
        "client_ready_files": client_ready_files,
        "files_info": files_info,
    })


@app.get("/tasks/{task_id}/download")
async def task_download(task_id: int):
    task = get_task(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Zadanie nie znalezione")
    output_dir = Path(task["output_dir"]) if task.get("output_dir") else None
    if not output_dir or not output_dir.exists():
        raise HTTPException(status_code=404, detail="Katalog wyjściowy nie istnieje")
    client_ready_dir = output_dir / "client-ready"
    source_dir = client_ready_dir if client_ready_dir.exists() and any(client_ready_dir.iterdir()) else output_dir
    # GF-017: ZIP budowany na dysku tymczasowym i streamowany do odpowiedzi —
    # wcześniej cały zip zbierał się w RAM procesu (io.BytesIO) — ryzyko OOM
    # przy dużych nagraniach/klatkach.
    import tempfile
    tmp = tempfile.NamedTemporaryFile(prefix=f"vtd_task_{task_id}_", suffix=".zip", delete=False)
    tmp.close()
    try:
        with zipfile.ZipFile(tmp.name, "w", zipfile.ZIP_DEFLATED) as zf:
            for file_path in source_dir.rglob("*"):
                if file_path.is_file():
                    if source_dir == output_dir and "frames" in file_path.parts:
                        continue
                    if file_path.suffix.lower() == ".wav":
                        continue
                    arcname = file_path.relative_to(source_dir)
                    zf.write(file_path, arcname)
    except Exception:
        Path(tmp.name).unlink(missing_ok=True)
        raise

    def _stream_and_cleanup():
        with open(tmp.name, "rb") as f:
            while True:
                chunk = f.read(64 * 1024)
                if not chunk:
                    break
                yield chunk
        Path(tmp.name).unlink(missing_ok=True)

    filename = f"task_{task_id}_client_ready.zip"
    return StreamingResponse(
        _stream_and_cleanup(),
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@app.get("/settings", response_class=HTMLResponse)
async def settings_get(request: Request):
    settings = load_settings()
    return templates.TemplateResponse(request, "settings.html", {
        "settings": settings,
        "models": ALLOWED_MODELS,
    })


@app.post("/settings")
async def settings_post(
    request: Request,
    enrich: str = Form(default=""),
    enrich_model: Optional[str] = Form(default=None),
    scene_threshold: str = Form(default="0.05"),
    track: str = Form(default="auto"),
    author: str = Form(default=""),
    frame_qa: str = Form(default=""),
    annotate: str = Form(default=""),
    nano_banana: str = Form(default=""),
):
    data = {}
    data["enrich"] = bool(enrich and enrich not in ("0", "false", "off"))
    # GF-016: walidacja względem ALLOWED_MODELS, fallback na wspólny default
    enrich_model = (enrich_model or "").strip() or DEFAULT_ENRICH_MODEL
    data["enrich_model"] = enrich_model if enrich_model in ALLOWED_MODELS else DEFAULT_ENRICH_MODEL
    try:
        data["scene_threshold"] = float(scene_threshold)
    except Exception:
        data["scene_threshold"] = 0.05
    data["track"] = track if track in ("mic", "mix", "meeting", "0", "1", "2", "auto") else "auto"
    data["author"] = author.strip()
    data["frame_qa"] = bool(frame_qa and frame_qa not in ("0", "false", "off"))
    data["annotate"] = bool(annotate and annotate not in ("0", "false", "off"))
    data["nano_banana"] = bool(nano_banana and nano_banana not in ("0", "false", "off"))
    saved = save_settings(data)
    return RedirectResponse(url="/settings", status_code=303)


@app.get("/api/track-detect")
async def api_track_detect(video: str = Query(...)):
    """JSON endpoint do auto-detekcji toru — ścieżka musi istnieć wewnątrz katalogu nagrań."""
    vp = resolve_video_path(video)
    try:
        from vtd.core.audio_detection import detect_best_track as _detect
        import concurrent.futures as _cf
        with _cf.ThreadPoolExecutor(max_workers=1) as ex:
            fut = ex.submit(_detect, str(vp))
            try:
                result = fut.result(timeout=30)
            except _cf.TimeoutError:
                raise HTTPException(status_code=504, detail="Detekcja przekroczyła limit czasu")
        return JSONResponse(result)
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e)[:300])

