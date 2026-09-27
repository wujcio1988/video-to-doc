"""
Proces roboczy (worker) obsługujący kolejkę zadań przetwarzania wideo w Studio.
"""
from __future__ import annotations

import re
import subprocess
import sys
import threading
import time
import unicodedata
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from vtd.config import load_config
from vtd.studio.db import get_task, list_tasks_by_status, update_task
from vtd.studio.settings_store import load_settings

_queue_lock = threading.Lock()
_queue: List[int] = []
_worker_thread: Optional[threading.Thread] = None
_running_task_id: Optional[int] = None
_recover_lock = threading.Lock()
_recovered = False

TASK_TIMEOUT_SECONDS = 4 * 60 * 60
TERMINATE_GRACE_SECONDS = 10.0


def slugify(text: str) -> str:
    """Konwertuje tekst na bezpieczny format nazwy katalogu/pliku."""
    text = text.strip().replace("ł", "l").replace("Ł", "L")
    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode("ascii")
    text = re.sub(r"[^0-9a-zA-Z]+", "_", text)
    text = re.sub(r"_+", "_", text).strip("_")
    return text or "unknown"


def build_output_dir(client: Optional[str], mode: str) -> Path:
    """Generuje ścieżkę katalogu wyjściowego dla zadania."""
    cfg = load_config()
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    base_out = cfg.storage.output_dir
    base_out.mkdir(parents=True, exist_ok=True)

    if client and client.strip() and client.strip() != "DO UZUPEŁNIENIA":
        slug = slugify(client)
        client_dir = base_out / slug
        client_dir.mkdir(parents=True, exist_ok=True)
        return client_dir / f"{mode}_{ts}"
    return base_out / f"{mode}_{ts}"


def enqueue_task(task_id: int) -> None:
    """Dodaje zadanie do kolejki przetwarzania."""
    with _queue_lock:
        if task_id not in _queue:
            _queue.append(task_id)


def start_worker() -> None:
    """Uruchamia wątek roboczy kolejki zadań."""
    global _worker_thread
    recover_tasks_on_startup()
    with _queue_lock:
        if _worker_thread is None or not _worker_thread.is_alive():
            _worker_thread = threading.Thread(target=_worker_loop, daemon=True)
            _worker_thread.start()


def recover_tasks_on_startup() -> None:
    """VTD-005: recovery po restarcie — przywraca spójność stanu zadań.

    Zadania w statusie `running` (przerwane przez restart) są oznaczane jako `error`,
    a zadania `queued` są ponownie dodawane do kolejki (wznowienie po restarcie).
    """
    global _recovered
    with _recover_lock:
        if _recovered:
            return
        _recovered = True
    now = datetime.now(timezone.utc).isoformat()
    for t in list_tasks_by_status("running"):
        update_task(
            t["id"],
            status="error",
            stage="failed",
            error_tail="Zadanie przerwane przez restart workera",
            finished_at=now,
        )
    for t in list_tasks_by_status("queued"):
        enqueue_task(t["id"])


def _terminate_process(proc: subprocess.Popen) -> None:
    """VTD-004: kończy proces potomny — terminate(), po grace period kill()."""
    try:
        proc.terminate()
    except Exception:
        pass
    try:
        proc.wait(timeout=TERMINATE_GRACE_SECONDS)
    except subprocess.TimeoutExpired:
        try:
            proc.kill()
        except Exception:
            pass
        try:
            proc.wait(timeout=TERMINATE_GRACE_SECONDS)
        except Exception:
            pass


def _worker_loop() -> None:
    global _running_task_id
    while True:
        task_id = None
        with _queue_lock:
            if _queue:
                task_id = _queue.pop(0)
                _running_task_id = task_id

        if task_id is None:
            time.sleep(1.0)
            continue

        try:
            _execute_task(task_id)
        except Exception as e:
            update_task(
                task_id,
                status="error",
                error_tail=f"Błąd krytyczny workera: {e}",
                finished_at=datetime.now(timezone.utc).isoformat(),
            )
        finally:
            with _queue_lock:
                _running_task_id = None


def _execute_task(task_id: int) -> None:
    task = get_task(task_id)
    if not task:
        return

    update_task(
        task_id,
        status="running",
        stage="init",
        progress_note="Przygotowanie zadania",
        started_at=datetime.now(timezone.utc).isoformat(),
    )

    # VTD-006: output_dir wylicza i tworzy TYLKO worker (ew. już ustawiony w rekordzie)
    if task.get("output_dir"):
        output_dir = Path(task["output_dir"])
    else:
        output_dir = build_output_dir(task.get("client"), task.get("mode", "manual"))
        update_task(task_id, output_dir=str(output_dir))

    cfg = load_config()
    settings = load_settings()

    log_path = cfg.storage.data_dir / f"task_{task_id}.log"
    update_task(task_id, log_path=str(log_path))

    py_bin = settings.get("pipeline_python") or sys.executable

    cmd = [
        py_bin,
        "-m",
        "vtd",
        "run",
        task["video_path"],
        "--output",
        str(output_dir),
        "--title",
        task.get("process") or task.get("video_name") or "Instrukcja",
        "--mode",
        task.get("mode", "manual"),
        "--track",
        task.get("track", "auto"),
        "--scene-threshold",
        str(float(task.get("scene_threshold", 0.05))),
        "--document-status",
        task.get("document_status", "DRAFT"),
        "--output-mode",
        task.get("output_mode", "both"),
    ]

    if task.get("client"):
        cmd.extend(["--client", task["client"]])
    if task.get("process"):
        cmd.extend(["--process", task["process"]])
    if task.get("module"):
        cmd.extend(["--module", task["module"]])
    if task.get("environment"):
        cmd.extend(["--environment", task["environment"]])
    if task.get("author"):
        cmd.extend(["--author", task["author"]])
    if task.get("enrich"):
        cmd.append("--enrich")
        if task.get("enrich_model"):
            cmd.extend(["--enrich-model", task["enrich_model"]])
    if task.get("frame_qa"):
        cmd.append("--frame-qa")
    if task.get("annotate") and task.get("mode") == "manual":
        cmd.append("--annotate")
    if task.get("max_step_seconds"):
        cmd.extend(["--max-step-seconds", str(float(task["max_step_seconds"]))])
    if task.get("grid_interval"):
        cmd.extend(["--grid-interval", str(float(task["grid_interval"]))])

    with open(log_path, "w", encoding="utf-8") as lf:
        lf.write(f"=== URUCHOMIENIE ZADANIA #{task_id} ===\nKomenda: {' '.join(cmd)}\n\n")
        lf.flush()

        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
        )

        error_lines = []

        def _pump_stdout() -> None:
            for line in proc.stdout:
                lf.write(line)
                lf.flush()
                error_lines.append(line)
                if len(error_lines) > 50:
                    error_lines.pop(0)

                # Mapowanie etapów
                if "Krok 1/6" in line:
                    update_task(task_id, stage="audio", progress_note="Ekstrakcja toru audio")
                elif "Krok 2/6" in line:
                    update_task(task_id, stage="silence", progress_note="Wykrywanie pauz w mowie")
                elif "Krok 3/6" in line:
                    update_task(task_id, stage="transcription", progress_note="Transkrypcja mowy (Whisper)")
                elif "Krok 4/6" in line:
                    update_task(task_id, stage="scenes", progress_note="Detekcja scen i wycinanie klatek")
                elif "Krok 5/6" in line:
                    update_task(task_id, stage="dedup", progress_note="Deduplikacja percepcyjna pHash")
                elif "Krok 6/6" in line:
                    update_task(task_id, stage="fusion", progress_note="Fuzja sygnałów i generowanie dokumentacji")
                elif "Frame QA" in line:
                    update_task(task_id, stage="frame_qa", progress_note="Weryfikacja czytelności klatek")
                elif "adnotacji AI" in line:
                    update_task(task_id, stage="annotations", progress_note="Generowanie adnotacji wizualnych")

        pump = threading.Thread(target=_pump_stdout, daemon=True)
        pump.start()

        timed_out = False
        try:
            proc.wait(timeout=TASK_TIMEOUT_SECONDS)
        except subprocess.TimeoutExpired:
            timed_out = True
            _terminate_process(proc)
        pump.join(timeout=TERMINATE_GRACE_SECONDS)

    finished = datetime.now(timezone.utc).isoformat()
    if timed_out:
        err_tail = "".join(error_lines[-15:])
        update_task(
            task_id,
            status="error",
            stage="failed",
            error_tail=f"Przekroczono limit czasu przetwarzania ({TASK_TIMEOUT_SECONDS:.0f}s); proces został przerwany.\n{err_tail}",
            finished_at=finished,
        )
    elif proc.returncode == 0:
        update_task(
            task_id,
            status="done",
            stage="complete",
            progress_note="Zakończono pomyślnie",
            finished_at=finished,
        )
    else:
        err_tail = "".join(error_lines[-15:])
        update_task(
            task_id,
            status="error",
            stage="failed",
            error_tail=err_tail or f"Kod błędu: {proc.returncode}",
            finished_at=finished,
        )
