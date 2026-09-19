import argparse
import json
import os
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Any, List

from vtd.cli import run_pipeline
from vtd.builders.html_builder import render_html_manual
from vtd.core.fusion import ManualStep

RECORDINGS_DIR = Path("/home/kozlo/erp-workspace/recordings")
STATE_FILE = RECORDINGS_DIR / ".processed_recordings.json"
ENV_FILE = Path.home() / ".hermes" / ".env"

def load_env() -> Dict[str, str]:
    env = {}
    if ENV_FILE.exists():
        for line in ENV_FILE.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                env[k.strip()] = v.strip().strip("'\"")
    return env

def load_state() -> Dict[str, Any]:
    if STATE_FILE.exists():
        try:
            return json.loads(STATE_FILE.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {"processed": {}}

def save_state(state: Dict[str, Any]):
    STATE_FILE.write_text(json.dumps(state, indent=2, ensure_ascii=False), encoding="utf-8")

def is_file_ready(file_path: Path, min_age_seconds: float = 8.0) -> bool:
    try:
        if not file_path.exists():
            return False
        st = file_path.stat()
        if st.st_size < 1000:
            return False
        age = time.time() - st.st_mtime
        if age < min_age_seconds:
            return False
        s0 = st.st_size
        time.sleep(2.5)
        s1 = file_path.stat().st_size
        if s0 != s1:
            return False
        cmd = [
            "ffprobe", "-v", "error",
            "-show_entries", "format=duration",
            "-of", "default=noprint_wrappers=1:nokey=1",
            str(file_path)
        ]
        res = subprocess.run(cmd, capture_output=True, text=True)
        if res.returncode != 0 or not res.stdout.strip():
            return False
        return True
    except Exception as e:
        print(f"[WATCHDOG] Błąd sprawdzania pliku {file_path.name}: {e}")
        return False

def send_telegram_document(docx_path: Path, caption: str, env: Dict[str, str]):
    """Manual-only: wysyłka dostępna WYŁĄCZNIE gdy jawnie włączono --allow-telegram. Domyślnie NIE WYSYŁA."""
    bot_token = env.get("TELEGRAM_BOT_TOKEN")
    chat_id = env.get("HERMES_ARTIFACT_CHAT", "6989419513")
    thread_id = env.get("HERMES_ARTIFACT_THREAD")
    if not bot_token or not docx_path.exists():
        return
    cmd = [
        "curl", "-s",
        "-F", f"chat_id={chat_id}",
        "-F", f"caption={caption}",
        "-F", f"document=@{docx_path}",
        f"https://api.telegram.org/bot{bot_token}/sendDocument"
    ]
    if thread_id and thread_id != "none":
        cmd.extend(["-F", f"message_thread_id={thread_id}"])
    res = subprocess.run(cmd, capture_output=True, text=True)
    if res.returncode == 0 and '"ok":true' in res.stdout:
        print(f"[WATCHDOG] Wysłano dokument Word na Telegram: {docx_path.name}")
    else:
        print(f"[WATCHDOG] Ostrzeżenie: wysyłka na Telegram nie powiodła się: {res.stdout}")

def send_telegram_artifact(html_path: Path, title: str):
    """Manual-only: wysyłka dostępna WYŁĄCZNIE gdy jawnie włączono --allow-telegram."""
    script = Path.home() / ".hermes" / "artifacts-server" / "scripts" / "hermes-send-artifact.sh"
    if script.exists() and html_path.exists():
        cmd = [str(script), str(html_path), title]
        res = subprocess.run(cmd, capture_output=True, text=True)
        if res.returncode == 0:
            print(f"[WATCHDOG] Wystawiono i wysłano Telegram Mini App: {title}")

def scan_and_process_once(mode: str = "manual", allow_telegram: bool = False) -> int:
    """
    Manual-first: domyślnie NIE wysyła do Telegrama. Wysyłka tylko gdy allow_telegram=True (jawna opt-in).
    Nie twierdzi o weryfikacji merytorycznej — status DRAFT / DO WERYFIKACJI.
    """
    state = load_state()
    processed_map = state.setdefault("processed", {})
    env = load_env()

    candidates: List[Path] = []
    for ext in ("*.mkv", "*.mp4"):
        for p in sorted(RECORDINGS_DIR.glob(ext)):
            if p.name.startswith(".") or "output" in p.name.lower():
                continue
            if p.name not in processed_map:
                candidates.append(p)

    if not candidates:
        return 0

    processed_count = 0
    for video_file in candidates:
        print(f"[WATCHDOG] Wykryto nowe nagranie: {video_file.name}. Sprawdzanie gotowości...")
        if not is_file_ready(video_file):
            print(f"[WATCHDOG] Plik {video_file.name} jest w trakcie zapisu/synchronizacji. Oczekiwanie...")
            continue

        clean_stem = video_file.stem.replace(" ", "_").replace(":", "-")
        out_dir = RECORDINGS_DIR / f"output_{clean_stem}"
        title = f"Instrukcja: {video_file.stem}"

        print(f"[WATCHDOG] Rozpoczynam przetwarzanie nagrania {video_file.name} -> {out_dir}... (manual-first, lokalny DRAFT, nie wysyłam do klienta)")
        t0 = time.time()
        try:
            run_pipeline(
                video_path=video_file,
                output_dir=out_dir,
                title=title,
                device="cuda",
                model_size="large-v3",
                scene_threshold=0.05,
                track="mic" if mode == "manual" else "meeting",
                mode=mode,
                enrich=True,
                document_status="DRAFT",
            )

            html_path = out_dir / "INSTRUKCJA.html"
            duration = time.time() - t0
            print(f"[WATCHDOG] Pomyślnie przetworzono {video_file.name} w {duration:.1f}s. Status: DRAFT / DO WERYFIKACJI — materiał lokalny do ręcznej weryfikacji, nie wysłany do klienta.")

            # Telegram — WYŁĄCZNIE gdy jawnie allow_telegram=True (manual-first)
            if allow_telegram:
                docx_path = out_dir / "INSTRUKCJA.docx"
                qa_path = out_dir / "QA_AUDIT.md"
                qa_status = "DRAFT / DO WERYFIKACJI — wymaga ręcznej weryfikacji, nie wysłano automatycznie do klienta"
                if qa_path.exists():
                    qa_text = qa_path.read_text(encoding="utf-8")
                    if "DRAFT" in qa_text:
                        qa_status = "DRAFT / DO WERYFIKACJI — materiał lokalny"
                caption = (
                    f"📹 Draft instrukcji gotowy (DRAFT / DO WERYFIKACJI) — nie wysłano do klienta\n\n"
                    f"• Plik: {video_file.name}\n"
                    f"• Czas przetwarzania: {duration:.1f}s na NVIDIA RTX 5070\n"
                    f"• Status: {qa_status}\n"
                    f"• Ścieżka lokalna: {out_dir}\n\n"
                    f"Materiał do ręcznej weryfikacji — nie wysyłaj automatycznie do klienta."
                )
                send_telegram_document(docx_path, caption, env)  # allow_telegram
                if html_path.exists():
                    send_telegram_artifact(html_path, f"📖 {video_file.stem} — DRAFT")  # allow_telegram
            else:
                print(f"[WATCHDOG] Manual-first: pominięto wysyłkę Telegram (wymaga --allow-telegram). Artefakty lokalne: {out_dir}")

            processed_map[video_file.name] = {
                "processed_at": datetime.now(timezone.utc).isoformat(),
                "output_dir": str(out_dir),
                "duration_seconds": round(duration, 2),
                "status": "success",
                "document_status": "DRAFT / DO WERYFIKACJI",
                "telegram_sent": bool(allow_telegram),
            }
            save_state(state)
            processed_count += 1

        except Exception as e:
            print(f"[WATCHDOG] BŁĄD przetwarzania {video_file.name}: {e}")
            processed_map[video_file.name] = {
                "processed_at": datetime.now(timezone.utc).isoformat(),
                "error": str(e),
                "status": "error"
            }
            save_state(state)

    return processed_count

def run_daemon(poll_interval: float = 6.0, mode: str = "manual", allow_telegram: bool = False):
    print(f"[WATCHDOG] Uruchomiono serwis nasłuchujący: {RECORDINGS_DIR} (manual-first, DRAFT / DO WERYFIKACJI, telegram={'ON - jawna wysyłka' if allow_telegram else 'OFF - lokalnie only'})")
    print(f"[WATCHDOG] Interwał skanowania: {poll_interval}s, Tryb: {mode}")
    while True:
        try:
            scan_and_process_once(mode=mode, allow_telegram=allow_telegram)
        except Exception as e:
            print(f"[WATCHDOG] Wyjątek w pętli głównej: {e}")
        time.sleep(poll_interval)

def main():
    parser = argparse.ArgumentParser(description="Watchdog automatycznego przetwarzania wideo do instrukcji ERP — manual-first, lokalny DRAFT (nie wysyła do Telegrama bez --allow-telegram)")
    parser.add_argument("--daemon", action="store_true", help="Uruchom w pętli ciągłej jako usługa w tle")
    parser.add_argument("--interval", type=float, default=6.0, help="Interwał sprawdzania w sekundach (domyślnie 6s)")
    parser.add_argument("--mode", choices=["manual", "meeting"], default="manual", help="Domyślny tryb pracy (manual/meeting)")
    parser.add_argument("--allow-telegram", action="store_true", help="Jawna zgoda na wysyłkę do Telegrama (domyślnie WYŁĄCZONA — manual-first). Bez tej flagi watchdog tylko przetwarza lokalnie.")
    args = parser.parse_args()

    if args.daemon:
        run_daemon(poll_interval=args.interval, mode=args.mode, allow_telegram=args.allow_telegram)
    else:
        processed = scan_and_process_once(mode=args.mode, allow_telegram=args.allow_telegram)
        print(f"[WATCHDOG] Zakończono pojedynczy skan. Przetworzono nagrań: {processed} (telegram={'wysłano' if args.allow_telegram else 'pominięto — manual-first'})")

if __name__ == "__main__":
    main()
