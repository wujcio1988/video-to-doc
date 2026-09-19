"""
Baza danych zadań SQLite dla Video-to-Doc Studio.
"""
from __future__ import annotations

import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from vtd.config import load_config

_lock = threading.Lock()

CREATE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS tasks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at TEXT NOT NULL,
    video_path TEXT NOT NULL,
    video_name TEXT NOT NULL,
    mode TEXT NOT NULL CHECK(mode IN ('manual','meeting')),
    client TEXT,
    process TEXT,
    module TEXT,
    environment TEXT,
    author TEXT,
    document_status TEXT NOT NULL DEFAULT 'DRAFT',
    output_mode TEXT NOT NULL DEFAULT 'both',
    scene_threshold REAL NOT NULL DEFAULT 0.05,
    track TEXT NOT NULL DEFAULT 'mic',
    enrich INTEGER NOT NULL DEFAULT 0,
    enrich_model TEXT,
    frame_qa INTEGER NOT NULL DEFAULT 0,
    max_step_seconds REAL NOT NULL DEFAULT 45.0,
    grid_interval REAL NOT NULL DEFAULT 30.0,
    annotate INTEGER NOT NULL DEFAULT 0,
    nano_banana INTEGER NOT NULL DEFAULT 0,
    output_dir TEXT,
    status TEXT NOT NULL CHECK(status IN ('queued','running','done','error')),
    stage TEXT,
    progress_note TEXT,
    log_path TEXT,
    started_at TEXT,
    finished_at TEXT,
    error_tail TEXT
);
"""


def get_db_path() -> Path:
    cfg = load_config()
    db_path = cfg.storage.data_dir / "tasks.db"
    db_path.parent.mkdir(parents=True, exist_ok=True)
    return db_path


def get_connection() -> sqlite3.Connection:
    conn = sqlite3.connect(str(get_db_path()), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    with _lock:
        conn = get_connection()
        try:
            conn.execute(CREATE_TABLE_SQL)
            conn.commit()
        finally:
            conn.close()


def create_task(data: Dict[str, Any]) -> int:
    init_db()
    with _lock:
        conn = get_connection()
        try:
            now = datetime.now(timezone.utc).isoformat()
            cursor = conn.execute(
                """
                INSERT INTO tasks (
                    created_at, video_path, video_name, mode, client, process, module,
                    environment, author, document_status, output_mode, scene_threshold,
                    track, enrich, enrich_model, frame_qa, max_step_seconds, grid_interval,
                    annotate, nano_banana, status, log_path
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'queued', ?)
                """,
                (
                    now,
                    data["video_path"],
                    data["video_name"],
                    data.get("mode", "manual"),
                    data.get("client"),
                    data.get("process"),
                    data.get("module"),
                    data.get("environment"),
                    data.get("author"),
                    data.get("document_status", "DRAFT"),
                    data.get("output_mode", "both"),
                    float(data.get("scene_threshold", 0.05)),
                    data.get("track", "auto"),
                    1 if data.get("enrich") else 0,
                    data.get("enrich_model"),
                    1 if data.get("frame_qa") else 0,
                    float(data.get("max_step_seconds", 45.0)),
                    float(data.get("grid_interval", 30.0)),
                    1 if data.get("annotate") else 0,
                    1 if data.get("nano_banana") else 0,
                    data.get("log_path"),
                ),
            )
            conn.commit()
            return cursor.lastrowid
        finally:
            conn.close()


def get_task(task_id: int) -> Optional[Dict[str, Any]]:
    init_db()
    with _lock:
        conn = get_connection()
        try:
            row = conn.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone()
            return dict(row) if row else None
        finally:
            conn.close()


def list_tasks(limit: int = 50) -> List[Dict[str, Any]]:
    init_db()
    with _lock:
        conn = get_connection()
        try:
            rows = conn.execute("SELECT * FROM tasks ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
            return [dict(r) for r in rows]
        finally:
            conn.close()


def update_task(task_id: int, **fields: Any) -> None:
    init_db()
    if not fields:
        return
    with _lock:
        conn = get_connection()
        try:
            keys = list(fields.keys())
            values = list(fields.values())
            set_clause = ", ".join(f"{k} = ?" for k in keys)
            values.append(task_id)
            conn.execute(f"UPDATE tasks SET {set_clause} WHERE id = ?", values)
            conn.commit()
        finally:
            conn.close()
