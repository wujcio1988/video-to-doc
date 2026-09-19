import argparse
import json
import sys
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional, Dict, Any
from vtd.config import PipelineConfig
from vtd.core.audio_extractor import extract_mic_audio, detect_silence, get_audio_stream_count
from vtd.core.transcriber import Transcriber, SpeechSegment
from vtd.core.frame_extractor import extract_scene_frames
from vtd.core.dedup import filter_duplicate_frames
from vtd.core.fusion import fuse_signals_into_steps_v2, validate_steps_timestamps, fuse_signals_into_steps
from vtd.builders.manual_builder import render_markdown_manual, build_llm_prompt, render_clean_transcript, render_docx_manual

# Stała dla Track 3 neutral labeling
SYSTEM_AUDIO_LABEL = "Desktop/system audio — niezweryfikowany rozmówca"


def resolve_device_and_compute(requested_device: str) -> tuple[str, str]:
    if requested_device == "cuda":
        return "cuda", "float16"
    elif requested_device == "cpu":
        return "cpu", "int8"
    try:
        import torch
        if torch.cuda.is_available():
            return "cuda", "float16"
    except Exception:
        pass
    return "cpu", "int8"


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="vtd",
        description="Generator instrukcji powdrożeniowych oraz transkrypcji spotkań z nagrań wideo OBS. Manual-first: wynik to lokalny DRAFT do weryfikacji, nie wysyłany do klienta."
    )
    sub = p.add_subparsers(dest="command")

    run_cmd = sub.add_parser("run", help="Uruchom pełny proces generowania instrukcji / protokołu z pliku wideo (manual-first, lokalny DRAFT)")
    run_cmd.add_argument("video_path", type=str, help="Ścieżka do nagrania MKV / MP4")
    run_cmd.add_argument("--output", "-o", type=str, default="manual_output", help="Katalog wyjściowy")
    run_cmd.add_argument("--title", "-t", type=str, default="Instrukcja Powdrożeniowa", help="Tytuł instrukcji / spotkania")
    run_cmd.add_argument("--device", choices=["auto", "cuda", "cpu"], default="auto", help="Urządzenie dla Whisper (auto: NVIDIA CUDA/float16 jeśli dostępne, inaczej cpu: int8)")
    run_cmd.add_argument("--model", default="large-v3", help="Model faster-whisper (np. large-v3, small)")
    run_cmd.add_argument("--scene-threshold", type=float, default=None, help="Próg detekcji zmian scen (domyślnie 0.35, dla ERP zalecane 0.05-0.08)")
    run_cmd.add_argument("--track", choices=["mic", "mix", "meeting", "0", "1", "2", "auto"], default="mic", help="Ścieżka audio: mic (Track 2 - mikrofon lektora), mix (Track 1 - miks), meeting (Track 2 + Track 3 - dialog; Track 3 to Desktop/system audio — niezweryfikowany rozmówca), auto (auto-detekcja najlepszego toru)")
    run_cmd.add_argument("--mode", choices=["manual", "meeting"], default="manual", help="Tryb pracy: manual (instrukcja krok po kroku), meeting (protokół spotkania — nie instrukcja)")
    run_cmd.add_argument("--enrich", action="store_true", help="Wzbogać draft przez model LLM (OmniRoute) — wynik nadal DRAFT / DO WERYFIKACJI")
    run_cmd.add_argument("--enrich-model", default="erp-manual", help="Model LLM w OmniRoute (np. erp-manual, antigravity/gemini-3.8-flash-tiered)")
    # Metadane wejściowe — manual-first
    run_cmd.add_argument("--client", type=str, default=None, help="Nazwa klienta (jeśli brak: DO UZUPEŁNIENIA; nie wyciągaj z nazwy pliku)")
    run_cmd.add_argument("--process", type=str, default=None, help="Proces biznesowy (np. Sprzedaż, Produkcja)")
    run_cmd.add_argument("--module", type=str, default=None, help="Moduł ERP (np. Handel, Produkcja by CTI)")
    run_cmd.add_argument("--environment", type=str, default=None, help="Środowisko / baza (np. TEST, PROD)")
    run_cmd.add_argument("--author", type=str, default=None, help="Autor / wdrożeniowiec")
    run_cmd.add_argument("--document-status", type=str, default="DRAFT", choices=["DRAFT", "DO WERYFIKACJI", "DRAFT / DO WERYFIKACJI"], help="Status dokumentu (domyślnie DRAFT — nigdy FINAL dla draftu)")
    run_cmd.add_argument("--output-mode", type=str, default="both", choices=["internal", "client-ready", "both"], help="Tryb wyjścia: internal (pełny lokalny), client-ready (bezpieczny draft bez prompt/wav/QA wewnętrznego), both (oba + manifest)")
    # B+C flags
    run_cmd.add_argument("--max-step-seconds", type=float, default=45.0, help="Max długość kroku przed podziałem narracyjnym (default 45s)")
    run_cmd.add_argument("--grid-interval", type=float, default=30.0, help="Interwał siatki kontrolnej klatek (default 30s)")
    run_cmd.add_argument("--frame-qa", action="store_true", help="Włącz kontrolę czytelności klatek przez LLM (OmniRoute localhost)")
    run_cmd.add_argument("--min-step-seconds", type=float, default=6.0, help="Minimalna długość kroku (default 6s)")
    run_cmd.add_argument("--annotate", action="store_true", help="Włącz adnotacje AI na zrzutach (tylko manual, po Frame QA)")
    run_cmd.add_argument("--nano-banana", action="store_true", help="Etap 4: wyostrzenie/oznaczenia klatek przez Gemini Flash Image (nano banana) z walidacją wierności OCR-diff; wynik w frames_enhanced/, odrzucone wracają do oryginału")
    run_cmd.add_argument("--nano-auto-accept", action="store_true", help="Automatyczna akceptacja wersji AI gdy walidacja OK (bez tego wymagany podgląd/akceptacja w Studio)")
    run_cmd.add_argument("--nano-mode", choices=["located", "composite"], default="located", help="Etap 5: located = AI wskazuje współrzędne (JSON), kod rysuje (default); composite = AI generuje warstwę (nano-banana-2, fallback located)")

    studio_cmd = sub.add_parser("studio", help="Uruchom interfejs webowy Video-to-Doc Studio")
    studio_cmd.add_argument("--host", type=str, default=None, help="Adres nasłuchiwania (domyślnie 127.0.0.1)")
    studio_cmd.add_argument("--port", type=int, default=None, help="Port serwera (domyślnie 9870)")

    rerender_cmd = sub.add_parser("rerender", help="Przeładuj i zregeneruj dokumentację z istniejącego folderu")
    rerender_cmd.add_argument("output_dir", type=str, help="Ścieżka do folderu z wygenerowaną dokumentacją")

    return p


def _build_metadata(
    title: str,
    mode: str,
    client: Optional[str],
    process: Optional[str],
    module: Optional[str],
    environment: Optional[str],
    author: Optional[str],
    document_status: str,
    output_mode: str,
    video_path: Path,
) -> Dict[str, Any]:
    # Nigdy nie wyciągaj klienta z nazwy pliku — jeśli brak, DO UZUPEŁNIENIA
    def _v(v: Optional[str]) -> str:
        if v is None or str(v).strip() == "":
            return "DO UZUPEŁNIENIA"
        return str(v).strip()
    # Status nigdy FINAL
    safe_status = document_status if document_status else "DRAFT"
    if safe_status.upper() == "FINAL":
        safe_status = "DRAFT"
    return {
        "title": title,
        "client": _v(client),
        "process": _v(process),
        "module": _v(module),
        "environment": _v(environment),
        "author": _v(author),
        "document_status": safe_status,
        "mode": mode,
        "output_mode": output_mode,
        "video_file": video_path.name,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "pipeline": "Hermes Video-to-Manual — manual-first, lokalny DRAFT",
        "note": "Materiał lokalny do ręcznej weryfikacji i edycji, nie wysyłany do klienta. Brak danych = DO POTWIERDZENIA / DO UZUPEŁNIENIA.",
    }


def run_pipeline(
    video_path: Path,
    output_dir: Path,
    title: str,
    device: str = "auto",
    model_size: str = "large-v3",
    scene_threshold: Optional[float] = None,
    track: str = "mic",
    mode: str = "manual",
    enrich: bool = False,
    enrich_model: str = "erp-manual",
    client: Optional[str] = None,
    process: Optional[str] = None,
    module: Optional[str] = None,
    environment: Optional[str] = None,
    author: Optional[str] = None,
    document_status: str = "DRAFT",
    output_mode: str = "both",
    max_step_seconds: float = 45.0,
    grid_interval: float = 30.0,
    frame_qa: bool = False,
    min_step_seconds: float = 6.0,
    annotate: bool = False,
    nano_banana: bool = False,
    nano_mode: str = "located",
    nano_auto_accept: bool = False,
):
    if not video_path.exists():
        print(f"BŁĄD: Plik wideo {video_path} nie istnieje!")
        sys.exit(1)

    chosen_device, chosen_compute = resolve_device_and_compute(device)

    if scene_threshold is not None:
        config = PipelineConfig(
            video_path=video_path,
            output_dir=output_dir,
            document_title=title,
            whisper_device=chosen_device,
            whisper_compute_type=chosen_compute,
            whisper_model=model_size,
            scene_threshold=scene_threshold
        )
    else:
        config = PipelineConfig(
            video_path=video_path,
            output_dir=output_dir,
            document_title=title,
            whisper_device=chosen_device,
            whisper_compute_type=chosen_compute,
            whisper_model=model_size
        )

    output_dir.mkdir(parents=True, exist_ok=True)
    frames_dir = output_dir / "frames"
    wav_path = output_dir / "mic_isolated.wav"
    try:
        stream_count = get_audio_stream_count(config.video_path)
    except Exception:
        stream_count = 1

    # Auto-detekcja toru audio (track == "auto")
    auto_detection = None
    if track == "auto":
        try:
            from vtd.core.audio_detection import detect_best_track as _detect_best_track
            auto_detection = _detect_best_track(str(config.video_path))
            # synchronizuj stream_count jeśli detekcja zwróciła inną liczbę
            if auto_detection.get("n_streams", 0) > 0:
                # zachowaj wartość z detekcji gdy różni się (ffprobe fallback)
                pass
            best_idx = auto_detection.get("best_track_index")
            best_label = auto_detection.get("best_track_label", "mic")
            has_speech = auto_detection.get("has_speech")
            # znajdź mean_volume dla best toru
            mean_vol_str = "n/a"
            if best_idx is not None:
                for t in auto_detection.get("tracks", []):
                    if t.get("index") == best_idx:
                        mean_vol_str = f"{t.get('mean_volume_db')} dB"
                        break
                # numer toru dla użytkownika: 1-based (Track N)
                track_num = best_idx + 1
                print(f"Track auto-detekcja: wybrano Track {track_num} ({best_label}, mean_volume {mean_vol_str})")
            else:
                # fallback gdy brak mowy -> wybierzemy tor 2 (mic) lub 1
                fallback_idx = 1 if stream_count > 1 else 0
                fallback_label = "mic" if fallback_idx == 1 else "mix"
                print(f"Track auto-detekcja: wybrano Track {fallback_idx+1} ({fallback_label}, fallback — brak wykrywalnej mowy)")
            if has_speech is False:
                print("OSTRZEŻENIE: Brak wykrywalnej narracji w nagraniu — dokument powstanie tylko z OCR klatek")
            elif has_speech is None and auto_detection.get("warning"):
                print(f"OSTRZEŻENIE auto-detekcji: {auto_detection.get('warning')}")
            if auto_detection.get("limited_to_120s"):
                print("INFO: pomiary głośności/mowy ograniczone do pierwszych 120s nagrania")
            if auto_detection.get("warning") and has_speech is not False and has_speech is not None:
                # wątpliwe / weak bez has_speech False już obsłużone, ale pokaż warning jeśli istnieje
                pass
        except Exception as e:
            print(f"OSTRZEŻENIE auto-detekcji: nie można zmierzyć toru ({e}) — użyto fallback mic")
            auto_detection = {
                "n_streams": stream_count,
                "best_track_index": None,
                "best_track_label": "mic",
                "tracks": [],
                "has_speech": None,
                "warning": f"nie można zmierzyć — błąd detekcji: {e}",
                "limited_to_120s": False,
            }

    print(f"=== Rozpoczęcie przetwarzania nagrania: {video_path.name} ===")
    print(f"Konfiguracja: urządzenie={config.whisper_device} ({config.whisper_compute_type}), model={config.whisper_model}, tryb={mode}, ścieżki_audio={stream_count}")
    print(f"Metadane: klient={client or 'DO UZUPEŁNIENIA'} | proces={process or 'DO UZUPEŁNIENIA'} | moduł={module or 'DO UZUPEŁNIENIA'} | env={environment or 'DO UZUPEŁNIENIA'} | status={document_status} | output={output_mode}")

    metadata = _build_metadata(title, mode, client, process, module, environment, author, document_status, output_mode, video_path)

    transcriber = Transcriber(
        model_size=config.whisper_model,
        device=config.whisper_device,
        compute_type=config.whisper_compute_type
    )

    transcripts: List[SpeechSegment] = []
    pauses = []

    if (track == "meeting" or mode == "meeting") and stream_count >= 3:
        print("Krok 1/6: Tryb spotkania — ekstrakcja dwukanałowa (Track 2: Wdrożeniowiec, Track 3: Desktop/system audio — niezweryfikowany rozmówca)...")
        wav_mic = output_dir / "mic_isolated.wav"
        wav_client = output_dir / "client_isolated.wav"
        extract_mic_audio(config.video_path, wav_mic, track_index=1)
        extract_mic_audio(config.video_path, wav_client, track_index=2)

        print("Krok 2/6: Wykrywanie pauz w mowie wdrożeniowca...")
        pauses = detect_silence(wav_mic, min_duration=config.min_pause_seconds)

        print("Krok 3/6: Transkrypcja mowy (NVIDIA faster-whisper)...")
        print("  -> Transkrypcja wypowiedzi wdrożeniowca (Track 2)...")
        segs_mic = transcriber.transcribe(wav_mic, language=config.whisper_language)
        for s in segs_mic:
            s.speaker = "Wdrożeniowiec"

        print(f"  -> Transkrypcja Desktop/system audio — niezweryfikowany rozmówca (Track 3, {len(segs_mic)} seg wdrożeniowca)...")
        segs_client = transcriber.transcribe(wav_client, language=config.whisper_language)
        for s in segs_client:
            s.speaker = SYSTEM_AUDIO_LABEL

        transcripts = sorted(segs_mic + segs_client, key=lambda x: x.start)
        print(f"  -> Łącznie uzyskano {len(transcripts)} segmentów dialogu.")
        wav_path = wav_mic
    else:
        if track == "auto":
            # auto-detekcja: wybierz best_track_index gdy dostępny
            if auto_detection and auto_detection.get("best_track_index") is not None:
                chosen_track = auto_detection["best_track_index"]
                # fallback gdy index poza zakresem (np. n_streams < 2)
                if chosen_track >= stream_count:
                    chosen_track = 1 if stream_count > 1 else 0
                # map label -> speaker/desc
                bl = auto_detection.get("best_track_label", "mic")
                if bl == "mix":
                    speaker_label = "Miks"
                    desc = "Track 1 (Miks lektora i systemu) [auto]"
                elif bl == "2":
                    speaker_label = SYSTEM_AUDIO_LABEL
                    desc = f"Track 3 ({SYSTEM_AUDIO_LABEL}) [auto]"
                else:
                    speaker_label = "Lektor"
                    desc = "Track 2 (Mikrofon lektora) [auto]"
            else:
                # brak best -> fallback jak mic
                chosen_track = 1 if stream_count > 1 else 0
                speaker_label = "Lektor"
                desc = "Track 2 (Mikrofon lektora) [auto-fallback]"
        elif track in ("mix", "0"):
            chosen_track = 0
            speaker_label = "Miks"
            desc = "Track 1 (Miks lektora i systemu)"
        elif track in ("2", "client", "desktop"):
            chosen_track = 2 if stream_count > 2 else 0
            speaker_label = SYSTEM_AUDIO_LABEL
            desc = f"Track 3 ({SYSTEM_AUDIO_LABEL})"
        else:
            chosen_track = 1 if stream_count > 1 else 0
            speaker_label = "Lektor"
            desc = "Track 2 (Mikrofon lektora)"

        print(f"Krok 1/6: Ekstrakcja toru audio ({desc})...")
        extract_mic_audio(config.video_path, wav_path, track_index=chosen_track)

        print("Krok 2/6: Wykrywanie przerw i pauz w mowie (silencedetect)...")
        pauses = detect_silence(wav_path, min_duration=config.min_pause_seconds)
        print(f"  -> Wykryto {len(pauses)} istotnych pauz w wypowiedzi.")

        print("Krok 3/6: Transkrypcja mowy po polsku (faster-whisper)...")
        transcripts = transcriber.transcribe(wav_path, language=config.whisper_language)
        for s in transcripts:
            s.speaker = speaker_label
        print(f"  -> Uzyskano {len(transcripts)} segmentów wypowiedzi.")

    print("Krok 4/6: Ekstrakcja klatek zmian scen (FFmpeg scene detection)...")
    raw_frames = extract_scene_frames(config.video_path, frames_dir, threshold=config.scene_threshold)
    print(f"  -> Wycięto {len(raw_frames)} kandydujących klatek zmian ekranu.")

    print("Krok 5/6: Deduplikacja percepcyjna klatek (pHash)...")
    unique_frames = filter_duplicate_frames(raw_frames, hamming_threshold=config.phash_threshold)
    print(f"  -> Pozostawiono {len(unique_frames)} unikalnych klatek do instrukcji.")

    print("Krok 6/6: Fuzja sygnałów i generowanie dokumentacji...")
    # Wyznacz duration dla siatki
    try:
        import subprocess as _sp, json as _js
        # ffprobe duration fallback
        video_duration = None
        if transcripts:
            video_duration = max(t.end for t in transcripts) if transcripts else None
        if video_duration is None:
            try:
                res = _sp.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "json", str(video_path)], capture_output=True, text=True, timeout=10)
                if res.returncode == 0:
                    d = _js.loads(res.stdout)
                    video_duration = float(d["format"]["duration"])
            except Exception:
                pass
    except Exception:
        video_duration = None

    steps = fuse_signals_into_steps_v2(
        unique_frames, pauses, transcripts,
        video_duration=video_duration,
        max_step_seconds=max_step_seconds,
        grid_interval=grid_interval,
        video_path=video_path,
        frames_dir=frames_dir,
        min_step_seconds=min_step_seconds,
    )
    print(f"  -> Skompilowano {len(steps)} logicznych kroków procedury.")

    # Walidacja timestampów
    ts_warnings = validate_steps_timestamps(steps, max_step_seconds=max_step_seconds)
    if ts_warnings:
        print("  [WALIDACJA] Ostrzeżenia timestampów:")
        for w in ts_warnings:
            print(f"    • {w}")

    # Track grid stats
    grid_count = sum(len(s.grid_frames) for s in steps)
    # Narrative splits heuristic: steps vs dedup frames
    narrative_splits_count = max(0, len(steps) - len(unique_frames)) if unique_frames else 0

    # ===== Frame QA (C2) =====
    frame_qa_results: List[Dict[str, Any]] = []
    frame_qa_reviews = 0
    frame_qa_swaps = 0
    frame_qa_unknown = 0
    if frame_qa:
        print("Kontrola czytelności klatek (LLM) — Frame QA...")
        try:
            from vtd.core.frame_qa import FrameReviewer, MAX_REVIEWS, improve_step_frame
            reviewer = FrameReviewer()
            # BATCH LIMIT: max 12 wywołań review na nagranie — przy większej liczbie kroków review tylko najdłuższe i te bez mowy
            MAX_R = MAX_REVIEWS
            # Determine which steps to review if > MAX_R
            if len(steps) > MAX_R:
                # Sort by priority: bez mowy first, then najdłuższe
                def qa_priority(s):
                    no_speech = 0 if not s.speech_text.strip() else 1
                    duration = s.end_time - s.start_time
                    return (no_speech, -duration)
                sorted_for_qa = sorted(steps, key=qa_priority)
                steps_to_review = sorted(sorted_for_qa[:MAX_R], key=lambda s: s.step_number)
                print(f"  -> Limit {MAX_R} review: wybrano {len(steps_to_review)} kroków (najdłuższe/bez mowy)")
            else:
                steps_to_review = steps

            # Map for quick lookup
            review_needed_set = set(s.step_number for s in steps_to_review)
            for s in steps:
                if s.step_number not in review_needed_set:
                    # not reviewed due to limit
                    frame_qa_results.append({"step_number": s.step_number, "verdict": "skipped", "reason": "Limit MAX_REVIEWS", "action": "pozostawiono"})
                    continue
                # For reviewed steps, call reviewer
                # Use step description: take first 500 chars of speech_text or enriched title later
                step_title = f"Krok {s.step_number}"
                step_desc = s.speech_text or "Brak opisu"
                result = reviewer.review_step_frame(s.step_number, step_title, step_desc, s.frame_path)
                frame_qa_reviews += 1
                verdict = result.get("verdict", "unknown")
                reason = result.get("reason", "")
                if verdict == "unknown":
                    frame_qa_unknown += 1
                action = "pozostawiono"
                if verdict in ("unclear", "wrong"):
                    # attempt improve
                    new_frame = improve_step_frame(video_path, s, frames_dir, reviewer)
                    if new_frame is not None and new_frame.exists():
                        # Re-review new frame to verify improvement
                        re_review = reviewer.review_step_frame(s.step_number, step_title, step_desc, new_frame)
                        frame_qa_reviews += 1  # second review counts? but we keep within limit? spec says max 12 reviews, but improve uses extra calls
                        # Note: spec MAX_REVIEWS counts review calls; we already exceeded if >12 but allow improve retries
                        re_verdict = re_review.get("verdict", "unknown")
                        if re_verdict == "ok":
                            # Podmień frame_path
                            old_path = s.frame_path
                            s.frame_path = new_frame
                            action = f"podmieniono klatkę na {new_frame.name} (stara: {old_path.name})"
                            frame_qa_swaps += 1
                            # Update verdict to ok for final
                            verdict = re_verdict
                            reason = re_review.get("reason", reason)
                        else:
                            # Not better, keep old but note attempt
                            action = f"próba re-cut {new_frame.name} — nadal {re_verdict}; pozostawiono oryginał + WYMAGA PODGLĄDU"
                            # keep original, but verdict remains original unclear/wrong
                    else:
                        action = "próba re-cut nieudana — pozostawiono + WYMAGA PODGLĄDU"
                frame_qa_results.append({"step_number": s.step_number, "verdict": verdict, "reason": reason, "action": action})
            print(f"  -> Frame QA: {frame_qa_reviews} review, {frame_qa_swaps} podmian, {frame_qa_unknown} unknown")
        except Exception as e:
            print(f"  [FRAME_QA] Błąd: {e}")
            frame_qa_results.append({"step_number": 0, "verdict": "unknown", "reason": str(e), "action": "błąd QA"})

    # ===== Adnotacje (PO Frame QA) =====
    annotations_map: Dict[int, List[Dict[str, Any]]] = {}
    annotated_frames_count = 0
    annotations_per_step: Dict[str, int] = {}
    annotated_dir = output_dir / "frames_annotated"
    if annotate and mode == "meeting":
        print("Adnotacje pominięte — tryb meeting (dokumentacja bez adnotacji).")
        annotate = False  # wyłacz dla dalszych sekcji
    if annotate and mode == "manual":
        print("Generowanie adnotacji AI na zrzutach (instrukcja)...")
        try:
            from vtd.core.element_locator import locate_words, plan_annotations
            from vtd.core.annotator import load_frame, annotate_frame
            # mapa verdict dla pomijania wrong/unclear/unknown
            qa_verdict_map = {r["step_number"]: r.get("verdict", "ok") for r in frame_qa_results} if frame_qa else {}
            # Track skipped annotations due frame_qa
            skipped_qa_steps = []
            annotated_dir.mkdir(parents=True, exist_ok=True)
            for s in steps:
                # pomijaj kroki z frame_qa != ok (wrong/unclear/unknown)
                if frame_qa and s.step_number in qa_verdict_map:
                    v = qa_verdict_map[s.step_number]
                    if v not in ("ok", "skipped"):
                        skipped_qa_steps.append(s.step_number)
                        annotations_map[s.step_number] = []
                        annotations_per_step[str(s.step_number)] = 0
                        continue
                # Wyodrebnij slowa do OCR: tokeny z speech_text + tytul kroku
                raw_text = f"{s.speech_text} Krok {s.step_number}"
                # tokenizacja: split by non-alnum, filter len >=3, unique, max 40
                import re as _re
                tokens = _re.findall(r"[\wąćęłńóśźżĄĆĘŁŃÓŚŹŻ]{3,}", raw_text)
                # deduplicate preserving order, lowercase not yet; keep original case but also filter stopwords
                stopwords = {"oraz", "przez", "jest", "oraz", "jest", "tego", "krok", "jest", "oraz", "będzie", "może", "dla", "oraz", "jest"}
                uniq = []
                seen_low = set()
                for tok in tokens:
                    low = tok.lower()
                    if low in seen_low or low in stopwords:
                        continue
                    seen_low.add(low)
                    uniq.append(tok)
                    if len(uniq) >= 40:
                        break
                # If still few tokens, add common ERP keywords from raw_text anyway
                located = locate_words(s.frame_path, uniq)
                # If OCR found nothing, try fallback with broader words? located will be {}
                title_for_llm = f"Krok {s.step_number}"
                desc_for_llm = s.speech_text or ""
                planned = plan_annotations(s.step_number, title_for_llm, desc_for_llm, located, llm_model="erp-code")
                # plan_annotations already enforces max 3
                if not planned:
                    annotations_map[s.step_number] = []
                    annotations_per_step[str(s.step_number)] = 0
                    continue
                # Render annotations onto copy
                try:
                    img = load_frame(s.frame_path)
                except Exception as e:
                    print(f"  [ANNOTATE] Krok {s.step_number}: nie można wczytać klatki {s.frame_path.name}: {e}")
                    annotations_map[s.step_number] = []
                    annotations_per_step[str(s.step_number)] = 0
                    continue
                # annotate_frame expects List[dict] with type, box, number
                ann_for_render = []
                for a in planned:
                    # ensure annotation has box
                    if "box" not in a or not a["box"]:
                        continue
                    ann_for_render.append(a)
                if not ann_for_render:
                    annotations_map[s.step_number] = []
                    annotations_per_step[str(s.step_number)] = 0
                    continue
                try:
                    annotated_img = annotate_frame(img, ann_for_render)
                except Exception as e:
                    print(f"  [ANNOTATE] Krok {s.step_number}: błąd rysowania {e}")
                    annotations_map[s.step_number] = []
                    annotations_per_step[str(s.step_number)] = 0
                    continue
                # Save as PNG without lossy compression
                out_name = s.frame_path.name  # preserve name like scene_0001.png or grid_...
                out_path = annotated_dir / out_name
                # Also handle grid frames? annotate grid frames similarly? spec says frames_annotated/scene_XXXX.png (lub grid_...)
                # If step has grid_frames, we could annotate them similarly but skip for now (annotate only main frame)
                try:
                    annotated_img.convert("RGB").save(str(out_path), "PNG", compress_level=0)
                    # Actually keep RGBA? PNG without lossy. Use compress_level 1 for not huge?
                    # Save as PNG (Pillow will handle)
                    if not out_path.exists():
                        annotated_img.save(str(out_path), "PNG")
                except Exception:
                    try:
                        annotated_img.save(str(out_path), "PNG")
                    except Exception as e2:
                        print(f"  [ANNOTATE] Krok {s.step_number}: zapis nieudany {e2}")
                        continue
                # Also annotate grid_frames if any (same logic simple)
                # keep minimal — annotate grid frames with same annotations? Skip separate locate for grid.
                # For each grid frame, create annotated copy as well (dim same)
                for gf in getattr(s, 'grid_frames', []) or []:
                    try:
                        g_img = load_frame(gf)
                        g_located = locate_words(gf, uniq)
                        g_planned = plan_annotations(s.step_number, title_for_llm, desc_for_llm, g_located, llm_model="erp-code")
                        if g_planned:
                            g_ann = annotate_frame(g_img, g_planned)
                            g_out = annotated_dir / gf.name
                            g_ann.convert("RGB").save(str(g_out), "PNG", compress_level=1)
                    except Exception:
                        pass
                annotations_map[s.step_number] = ann_for_render
                annotations_per_step[str(s.step_number)] = len(ann_for_render)
                annotated_frames_count += 1
                print(f"  -> Krok {s.step_number}: {len(ann_for_render)} adnotacji ({', '.join(a.get('label', a.get('element','')) for a in ann_for_render)})")
            # podsumowanie
            if skipped_qa_steps:
                print(f"  -> Pominięto adnotacje dla {len(skipped_qa_steps)} kroków z frame_qa != ok: {skipped_qa_steps}")
        except Exception as e:
            print(f"  [ANNOTATE] Błąd globalny adnotacji: {e}")
            # ensure map exists
            for s in steps:
                if s.step_number not in annotations_map:
                    annotations_map[s.step_number] = []
                    annotations_per_step[str(s.step_number)] = 0
    else:
        if not annotate:
            # ensure defaults
            for s in steps:
                annotations_per_step[str(s.step_number)] = 0

    # ===== Etap 4: NANO BANANA (Gemini Flash Image) — enhancement/oznaczenia klatek =====
    nano_stats = {"enabled": False, "processed": 0, "accepted": 0, "rejected": 0, "cost_usd": 0.0, "details": []}
    if 'enriched_data' not in locals():
        enriched_data = None
    if nano_banana and mode == "manual":
        try:
            from vtd.core.image_enhancer import (
                enhance_frame_composite, enhance_frame_located, resolve_gemini_key, EnhanceError,
            )
            api_key = resolve_gemini_key()
            if not api_key:
                print("  [NANO] Brak GEMINI_API_KEY — pomijam enhancement (ustaw klucz w ~/.hermes/.env).")
            else:
                nano_stats["enabled"] = True
                enhanced_dir = output_dir / "frames_enhanced"
                enhanced_dir.mkdir(parents=True, exist_ok=True)
                qa_map = {r["step_number"]: r.get("verdict", "ok") for r in frame_qa_results} if frame_qa else {}
                mode_label = "LOCATED (locator+draw)" if nano_mode == "located" else "COMPOSITE (nano-banana-2)"
                print(f"Nano Banana ({mode_label}) + weryfikator gemini-flash...")
                for s in steps:
                    if s.step_number in qa_map and qa_map[s.step_number] in ("wrong", "skipped"):
                        nano_stats["details"].append({"step": s.step_number, "status": "pominięty (frame_qa)"})
                        continue
                    # cel kroku: opis z enrichmentu > narracja; hinty OCR NIE idą do prompta (mylą)
                    es_desc = ""
                    if enriched_data and "steps" in enriched_data:
                        for _es in enriched_data["steps"]:
                            if _es.get("step_number") == s.step_number:
                                es_desc = (_es.get("description") or "").strip()
                                break
                    target_description = es_desc or (s.speech_text or "").strip()
                    step_text = s.speech_text or ""
                    try:
                        _enhancer = enhance_frame_located if nano_mode == "located" else enhance_frame_composite
                        res = _enhancer(
                            s.frame_path, api_key,
                            target_description=target_description,
                            step_text=step_text,
                            output_dir=output_dir / "frames_enhanced",
                            verify_goal=True,
                            step_number=s.step_number,
                        )
                    except EnhanceError as e:
                        print(f"  [NANO] Krok {s.step_number}: błąd modelu: {e}")
                        nano_stats["details"].append({"step": s.step_number, "status": "błąd modelu", "error": str(e)[:120]})
                        continue
                    nano_stats["processed"] += 1
                    nano_stats["cost_usd"] += res.get("cost_usd", 0.0)
                    if res["status"] != "accepted":
                        print(f"  [NANO] Krok {s.step_number}: REJECTED — {res['reason']}")
                        nano_stats["rejected"] += 1
                        nano_stats["details"].append({"step": s.step_number, "status": "rejected", "reason": str(res["reason"])[:160], "cost_usd": res.get("cost_usd", 0.0)})
                        continue
                    if nano_auto_accept:
                        (output_dir / "frames_enhanced" / (Path(s.frame_path.name).stem + "_accepted")).write_text("auto", encoding="utf-8")
                    nano_stats["accepted"] += 1
                    nano_stats["details"].append({"step": s.step_number, "status": "accepted", "file": s.frame_path.name, "cost_usd": res.get("cost_usd", 0.0), "target_ok": res.get("target_ok")})
                print(f"  -> Nano Banana composite: {nano_stats['accepted']} przyjętych, {nano_stats['rejected']} odrzuconych (jednolitość+cel), koszt ~${nano_stats['cost_usd']:.3f}")
        except Exception as e:
            print(f"  [NANO] Błąd globalny enhancement: {e}")

    # Statystyki kompletności
    raw_count = len(raw_frames)
    dedup_count = len(unique_frames)
    steps_without_evidence = sum(1 for s in steps if not s.speech_text.strip())
    segments_without_frame = max(0, len(transcripts) - len(steps)) if transcripts else 0
    completeness = {
        "segments": len(transcripts),
        "frames_raw": raw_count,
        "frames_dedup": dedup_count,
        "steps": len(steps),
        "steps_without_speech": steps_without_evidence,
        "segments_without_frame": segments_without_frame,
        "timestamp_warnings": ts_warnings,
        "note": "Word-level timestamps nie są używane — pipeline segment-level. Dowód per krok: timestamp start/end + nazwa klatki.",
        "narrative_splits": narrative_splits_count,
        "grid_frames": grid_count,
        "max_step_seconds": max_step_seconds,
        "grid_interval": grid_interval,
        "frame_qa": {
            "enabled": frame_qa,
            "reviews": frame_qa_reviews,
            "swaps": frame_qa_swaps,
            "unknown": frame_qa_unknown,
            "max_reviews": 12,
        } if frame_qa else {"enabled": False},
    }
    metadata["stats"] = completeness
    if auto_detection is not None:
        metadata["track_auto_detection"] = auto_detection
        metadata["track_requested"] = track
        # also store chosen track for traceability
        if auto_detection.get("best_track_index") is not None:
            metadata["track_chosen"] = auto_detection.get("best_track_label")
        else:
            metadata["track_chosen"] = "mic"
    metadata["enrich_status"] = "not_requested" if not enrich else "pending"
    # Store QA results for enrich context
    metadata["frame_qa_results"] = frame_qa_results if frame_qa else []
    # Store fusion params
    metadata["fusion_params"] = {"max_step_seconds": max_step_seconds, "grid_interval": grid_interval, "min_step_seconds": min_step_seconds}
    # Annotations metadata
    # annotate flag may have been disabled for meeting; ensure metadata reflects final state
    metadata["annotate"] = bool(annotate and mode == "manual") if 'annotate' in locals() else False
    metadata["annotations"] = annotations_per_step if 'annotations_per_step' in locals() else {}
    metadata["annotated_frames"] = annotated_frames_count if 'annotated_frames_count' in locals() else 0
    metadata["nano_banana"] = bool(nano_stats.get("enabled"))
    metadata["nano_banana_stats"] = nano_stats
    metadata["annotations_detail"] = {str(k): [{"type": a.get("type"), "element": a.get("element"), "label": a.get("label"), "box": a.get("box")} for a in v] for k, v in (annotations_map.items() if 'annotations_map' in locals() else {})}

    enriched_data = None
    qa_issues: List[str] = []
    if enrich:
        try:
            print(f"Wzbogacanie treści draftu przez model LLM ({enrich_model}) — tryb {mode}...")
            from vtd.core.enricher import enrich_steps_with_llm
            # Pass frame QA context: add reason to steps for enricher prompt
            # Enricher will read metadata frame_qa_results if available — we inject via steps extra attr
            # Simplest: temporarily append reason to speech_text for enricher
            if frame_qa and frame_qa_results:
                qa_map = {r["step_number"]: r for r in frame_qa_results}
                for s in steps:
                    r = qa_map.get(s.step_number)
                    if r and r.get("verdict") not in ("ok", "skipped"):
                        # append one line context for LLM redaction
                        s.speech_text = (s.speech_text + f" [Frame QA: {r['verdict']} — {r['reason']}]").strip()
            enriched_data = enrich_steps_with_llm(
                title=title, steps=steps, model=enrich_model, timeout=180,
                client=client, process=process, module=module, environment=environment,
                author=author, document_status=document_status, mode=mode,
            )
            if enriched_data:
                print("  -> Pomyślnie zredagowano draft przez LLM (status DRAFT / DO WERYFIKACJI).")
                print("Bramka QA: Ograniczona kontrola spójności DRAFT (bez połączenia z bazą ERP)...")
                from vtd.core.qa_verifier import audit_and_fix_manual
                enriched_data, qa_issues = audit_and_fix_manual(
                    enriched_data=enriched_data,
                    steps=steps,
                    title=title,
                    model="antigravity/gemini-3.8-flash-tiered"
                )
                if qa_issues:
                    print("  [QA AUDYT — DRAFT / DO WERYFIKACJI] Uwagi do ręcznej weryfikacji:")
                    for issue in qa_issues:
                        print(f"    • {issue}")
                else:
                    print("  [QA AUDYT] DRAFT / DO WERYFIKACJI — brak automatycznych uwag, wymagana ręczna weryfikacja.")

                qa_report_path = output_dir / "QA_AUDIT.md"
                qa_md = [
                    f"# Raport Kontroli Spójności DRAFT: {title}",
                    "",
                    f"**Status dokumentu:** DRAFT / DO WERYFIKACJI — wymaga ręcznej weryfikacji przez wdrożeniowca. Nie jest to weryfikacja merytoryczna z bazą ERP.",
                    f"**Model audytujący (ograniczony zakres):** {enrich_model}",
                    f"**Data:** {datetime.now(timezone.utc).isoformat()}",
                    "",
                    "## Uwagi do weryfikacji (nie są to potwierdzenia z bazy ERP):",
                    ""
                ]
                if qa_issues:
                    for iss in qa_issues:
                        qa_md.append(f"- {iss}")
                else:
                    qa_md.append("- Brak automatycznych uwag. Dokument nadal wymaga ręcznej weryfikacji — nie twierdzimy o zgodności z bazą ERP.")
                qa_md.append("")
                qa_md.append("> **Uwaga:** Ten raport to jedynie lokalna kontrola spójności draftu. Nie wykonano połączenia z bazą ERP. Status zawsze DRAFT / DO WERYFIKACJI.")
                qa_report_path.write_text("\n".join(qa_md), encoding="utf-8")
                metadata["enrich_status"] = "success"
                metadata["qa_issues"] = qa_issues
            else:
                print("  -> Ostrzeżenie: wzbogacanie LLM zwróciło None — draft bez enrich, status DRAFT / DO WERYFIKACJI.")
                metadata["enrich_status"] = "failed"
                metadata["qa_issues"] = ["Enricher zwrócił None — brak wzbogacenia, draft wymaga ręcznej weryfikacji."]
                # Zapisz ostrzeżenie
                (output_dir / "ENRICHER_WARNING.md").write_text(
                    f"# Ostrzeżenie Enricher: {title}\n\n**Status:** DRAFT / DO WERYFIKACJI\n\nEnricher nie zwrócił danych (None / błąd). Dokument nie jest gotowy jako jakościowy — wymaga ręcznej weryfikacji i uzupełnienia. Nie wysyłaj do klienta.\n",
                    encoding="utf-8"
                )
        except Exception as e:
            print(f"  -> Ostrzeżenie: wzbogacanie LLM pominięte ({e}). Status DRAFT / DO WERYFIKACJI.")
            metadata["enrich_status"] = f"error: {e}"
            metadata["qa_issues"] = [f"Błąd enrichera: {e} — draft wymaga ręcznej weryfikacji."]
            (output_dir / "ENRICHER_WARNING.md").write_text(
                f"# Błąd Enrichera: {title}\n\n**Status:** DRAFT / DO WERYFIKACJI\n\nBłąd: {e}\n\nDokument nie jest gotowy jako jakościowy — wymaga ręcznej weryfikacji. Nie wysyłaj do klienta.\n",
                encoding="utf-8"
            )

    # Zapisz metadata.json — lokalny plik prawdy o metadanych (nie nazwa pliku)
    metadata_path = output_dir / "metadata.json"
    metadata_path.write_text(json.dumps(metadata, indent=2, ensure_ascii=False), encoding="utf-8")

    # Zapisz raport kompletności
    completeness_path = output_dir / "COMPLETENESS_REPORT.md"
    comp_lines = [
        f"# Raport Kompletności: {title}",
        "",
        f"**Status:** {metadata['document_status']} / DO WERYFIKACJI — materiał lokalny",
        f"**Tryb:** {mode}",
        f"**Data:** {metadata['generated_at']}",
        "",
        "## Statystyki pipeline",
        f"- Liczba segmentów transkrypcji: {completeness['segments']}",
        f"- Liczba klatek surowych: {completeness['frames_raw']}",
        f"- Liczba klatek po dedup: {completeness['frames_dedup']}",
        f"- Liczba kroków / tematów: {completeness['steps']}",
        f"- Kroki bez dowodu mowy: {completeness['steps_without_speech']}",
        f"- Segmenty bez przypisanej klatki: {completeness['segments_without_frame']}",
        f"- Rozbicia narracyjne: {narrative_splits_count}",
        f"- Klatki siatkowe: {grid_count}",
        f"- max_step_seconds: {max_step_seconds}",
        f"- grid_interval: {grid_interval}",
        "",
        "## Walidacja",
        f"- Ostrzeżenia timestampów: {len(ts_warnings)}",
    ]
    for w in ts_warnings:
        comp_lines.append(f"  - {w}")
    if not ts_warnings:
        comp_lines.append("  - Brak ostrzeżeń — wszystkie zakresy end >= start, monotoniczne.")
    # Rozbicia narracyjne sekcja
    comp_lines.extend([
        "",
        "## Rozbicia narracyjne",
    ])
    if narrative_splits_count > 0:
        # which steps are narrative splits? those beyond original frame count
        # We expose logic: steps created by B1a/B1b
        comp_lines.append(f"- Liczba kroków utworzonych przez podział narracyjny (max_step / pauza >8s): {narrative_splits_count}")
        # list numbers beyond dedup count? more precise: steps whose start not coinciding with original frame timestamp
        orig_timestamps = set(f.timestamp for f in unique_frames)
        split_steps = [s for s in steps if s.start_time not in orig_timestamps or abs(s.start_time - min(orig_timestamps, key=lambda x: abs(x - s.start_time))) > 0.01]
        # Simplify: show numbers of steps that were split
        split_nums = [str(s.step_number) for s in steps if s.step_number > len(unique_frames) or (len(unique_frames) >0 and s.start_time not in orig_timestamps)]
        if split_nums:
            comp_lines.append(f"- Numery kroków z podziału: {', '.join(split_nums[:20])}")
        else:
            comp_lines.append(f"- Numery kroków: — (wszystkie z klatek, rozbicia nie wymagane)")
    else:
        comp_lines.append("- Brak dodatkowych rozbicia narracyjnych — kroki zgodne z klatkami scen.")
        comp_lines.append(f"  (max_step_seconds={max_step_seconds}, pauza >8s)")

    # Frame QA sekcja
    if frame_qa:
        comp_lines.extend([
            "",
            "## Frame QA",
            f"- Review wykonano: {frame_qa_reviews} / max 12",
            f"- Podmian klatek: {frame_qa_swaps}",
            f"- Unknown: {frame_qa_unknown}",
            "",
            "| Krok | Verdict | Reason | Akcja |",
            "|---|---|---|---|---|",
        ])
        for r in frame_qa_results:
            comp_lines.append(f"| {r['step_number']} | {r['verdict']} | {r['reason'][:80]} | {r['action'][:60]} |")
        # Add manual preview needed count
        need_preview = sum(1 for r in frame_qa_results if r['verdict'] not in ("ok", "skipped") )
        comp_lines.append("")
        comp_lines.append(f"- WYMAGA PODGLĄDU RĘCZNEGO: {need_preview} kroków")

    # Adnotacje sekcja
    annotate_enabled = bool(annotate and mode == "manual") if 'annotate' in locals() else False
    # ensure annotations_map available
    if 'annotations_map' not in locals():
        annotations_map = {}
    if 'annotations_per_step' not in locals():
        annotations_per_step = {}
    comp_lines.extend([
        "",
        "## Adnotacje",
    ])
    if annotate_enabled:
        total_ann = sum(len(v) for v in annotations_map.values())
        comp_lines.append(f"- Kroki z adnotacjami: {sum(1 for v in annotations_map.values() if v)} / {len(steps)}")
        comp_lines.append(f"- Łącznie adnotacji: {total_ann}")
        comp_lines.append(f"- Katalog: frames_annotated/ ({annotated_frames_count if 'annotated_frames_count' in locals() else 0} plików)")
        comp_lines.append("")
        comp_lines.append("| Krok | Adnotacje | Elementy |")
        comp_lines.append("|---|---|---|")
        for s in steps:
            anns = annotations_map.get(s.step_number, [])
            cnt = len(anns)
            elems = ", ".join(a.get("element", a.get("label", "")) for a in anns) if anns else "—"
            if frame_qa and s.step_number in (qa_verdict_map if 'qa_verdict_map' in locals() else {}):
                v = qa_verdict_map.get(s.step_number, "ok")
                if v not in ("ok", "skipped"):
                    elems = f"pominięto (frame_qa={v})"
                    cnt = 0
            comp_lines.append(f"| {s.step_number} | {cnt} | {elems} |")
        if 'skipped_qa_steps' in locals() and skipped_qa_steps:
            comp_lines.append("")
            comp_lines.append(f"- Kroki pominięte (frame_qa != ok): {', '.join(str(x) for x in skipped_qa_steps)}")
    else:
        if mode == "meeting":
            comp_lines.append("- Adnotacje wyłączone — tryb meeting (dokumentacja bez adnotacji).")
        else:
            comp_lines.append("- Adnotacje wyłączone (--annotate nie podano).")

    # Sekcja Nano Banana (Etap 4)
    comp_lines.extend(["", "## Nano Banana (AI enhancement)"])
    _ns = nano_stats if 'nano_stats' in locals() else {"enabled": False}
    if _ns.get("enabled"):
        comp_lines.append(f"- Model: gemini-3.1-flash-image (Google AI Studio)")
        comp_lines.append(f"- Przetworzone klatki: {_ns.get('processed', 0)} | przyjęte: {_ns.get('accepted', 0)} | odrzucone (walidacja wierności): {_ns.get('rejected', 0)}")
        comp_lines.append(f"- Szacowany koszt: ${_ns.get('cost_usd', 0.0):.3f}")
        comp_lines.append(f"- Katalog: frames_enhanced/ (wersje AI wymagają akceptacji: plik *_accepted, auto przy --nano-auto-accept)")
        comp_lines.append("")
        comp_lines.append("| Krok | Status | Uwagi |")
        comp_lines.append("|---|---|---|")
        _nd = _ns.get("details")
        for d in (list(_nd) if isinstance(_nd, list) else []):
            comp_lines.append(f"| {d.get('step', '—')} | {d.get('status', '—')} | {d.get('reason', d.get('error', ''))[:80]} |")
    else:
        comp_lines.append("- Wyłączone (--nano-banana nie podano lub brak GEMINI_API_KEY).")

    # Enriched source map for proof table
    _enriched_proof_map: Dict[str, Any] = {}
    if enriched_data and "steps" in enriched_data:
        for _es in enriched_data["steps"]:
            if "step_number" in _es:
                _enriched_proof_map[_es["step_number"]] = _es
    comp_lines.extend([
        "",
        "## Dowody per krok",
        "Każdy krok / temat zawiera: timestamp start/end, nazwę klatki, źródło (narration/transcript/OCR/reference material/model suggestion).",
        "Word-level timestamps: nie używane w obecnym pipeline — zachowano segment-level jako punkt rozszerzenia (WhisperX planowany).",
        "",
        "| Krok | Klatka | Start | Koniec | Źródło |",
        "|---|---|---|---|---|",
    ])
    for _s in steps:
        _es_info = _enriched_proof_map.get(_s.step_number, {})
        _src = _es_info.get("source", "narration/transcript") if isinstance(_es_info, dict) else "narration/transcript"
        # grid frame display
        if _s.grid_frames:
            grid_names = " + ".join(p.name for p in _s.grid_frames)
            klatka_cell = f"{_s.frame_path.name} + {grid_names}"
        else:
            klatka_cell = _s.frame_path.name
        comp_lines.append(f"| {_s.step_number} | {klatka_cell} | {_s.start_time:.1f}s | {_s.end_time:.1f}s | {_src} |")
    comp_lines.extend([
        "",
        "> **Uwaga:** Nigdy nie oznaczaj draftu jako FINAL. Wymagana ręczna weryfikacja przed wysyłką do klienta.",
    ])
    # Track auto-detekcja sekcja gdy auto było użyte
    if auto_detection is not None:
        comp_lines.append("")
        comp_lines.append("## Track auto-detekcja")
        limited_note = " (pomiar na pierwszych 120s)" if auto_detection.get("limited_to_120s") else ""
        comp_lines.append(f"- Tryb track: auto{limited_note}")
        comp_lines.append(f"- Liczba strumieni audio: {auto_detection.get('n_streams')}")
        if auto_detection.get("best_track_index") is not None:
            comp_lines.append(f"- Wybrano: Track {auto_detection['best_track_index']+1} ({auto_detection.get('best_track_label')})")
        else:
            comp_lines.append(f"- Wybrano: fallback mic (brak wykrywalnej mowy)")
        comp_lines.append(f"- has_speech: {auto_detection.get('has_speech')}")
        if auto_detection.get("warning"):
            comp_lines.append(f"- Ostrzeżenie: {auto_detection.get('warning')}")
        comp_lines.append("")
        comp_lines.append("| Tor (index) | mean_volume dB | max_volume dB | speech_ratio | verdict | duration_measured |")
        comp_lines.append("|---|---|---|---|---|---|---|")
        for t in auto_detection.get("tracks", []):
            comp_lines.append(f"| {t.get('index')} | {t.get('mean_volume_db')} | {t.get('max_volume_db')} | {t.get('speech_ratio')} | {t.get('verdict')} | {t.get('duration_measured')}s |")
        if not auto_detection.get("tracks"):
            comp_lines.append("| (brak danych) | - | - | - | - | - |")
        if auto_detection.get("limited_to_120s"):
            comp_lines.append("")
            comp_lines.append("> Pomiar ograniczony do pierwszych 120s nagrania (długie nagranie).")
    completeness_path.write_text("\n".join(comp_lines), encoding="utf-8")

    # Prepare WYMAGA PODGLĄDU map for doc renderers
    qa_need_preview_map: Dict[int, str] = {}
    if frame_qa:
        for r in frame_qa_results:
            if r.get("verdict") not in ("ok", "skipped") and r.get("step_number", 0) > 0:
                qa_need_preview_map[r["step_number"]] = r.get("reason", "")

    # 1. Zapis szkieletu Markdown — z metadanymi i trybem; attach QA preview map via enriched_data extra
    # We inject QA preview into enriched_data steps if enrich not used? For MD builder we pass extra param via metadata
    # Simpler: patch enriched_data with callout for QA if no enriched_data
    # For MD/DOCX/HTML we need to add UWAGA per step — we handle via manual_builder/html_builder reading metadata?
    # Instead we pass qa_need_preview_map as part of metadata and modify renderers to check it.
    # For now, ensure metadata contains it, and renderers will read it if they support.
    # To support existing renderers, we inject a synthetic enriched_data entry for non-enriched case
    if qa_need_preview_map and not enriched_data:
        enriched_data = {"steps": []}
        for s in steps:
            if s.step_number in qa_need_preview_map:
                enriched_data["steps"].append({"step_number": s.step_number, "qa_preview": qa_need_preview_map[s.step_number]})
    elif qa_need_preview_map and enriched_data:
        # merge
        existing_map = {es["step_number"]: es for es in enriched_data.get("steps", []) if "step_number" in es}
        for sn, reason in qa_need_preview_map.items():
            if sn in existing_map:
                existing_map[sn]["qa_preview"] = reason
            else:
                enriched_data.setdefault("steps", []).append({"step_number": sn, "qa_preview": reason})

    # Wstrzyknij adnotacje do enriched_data dla renderers (legenda badge)
    if 'annotations_map' in locals() and annotations_map:
        if enriched_data is None:
            enriched_data = {"steps": []}
        if "steps" not in enriched_data:
            enriched_data["steps"] = []
        existing_ann_map = {es["step_number"]: es for es in enriched_data.get("steps", []) if "step_number" in es}
        for sn, anns in annotations_map.items():
            if not anns:
                continue
            # legend: "1 — Dodaj • 2 — Surowce"
            badge_parts = []
            for a in anns:
                if a.get("type") == "badge" and "number" in a:
                    badge_parts.append(f"{a['number']} — {a.get('label', a.get('element',''))}")
                elif "element" in a:
                    # for arrow/highlight use element as label
                    badge_parts.append(a.get("label", a.get("element","")))
            legend = " • ".join(badge_parts) if badge_parts else ", ".join(a.get("label","") for a in anns)
            if sn in existing_ann_map:
                existing_ann_map[sn]["annotations"] = anns
                existing_ann_map[sn]["annotation_legend"] = legend
            else:
                enriched_data["steps"].append({"step_number": sn, "annotations": anns, "annotation_legend": legend})

    doc = render_markdown_manual(title=title, steps=steps, base_dir=output_dir, enriched_data=enriched_data, metadata=metadata, mode=mode)
    doc_path = output_dir / "INSTRUKCJA.md"
    doc_path.write_text(doc, encoding="utf-8")

    # 2. Transkrypcja
    transcript_md = render_clean_transcript(title=title, transcripts=transcripts, mode=mode, metadata=metadata)
    transcript_path = output_dir / "TRANSKRYPCJA.md"
    transcript_path.write_text(transcript_md, encoding="utf-8")

    # 3. DOCX
    docx_path = output_dir / "INSTRUKCJA.docx"
    render_docx_manual(title=title, steps=steps, output_docx=docx_path, enriched_data=enriched_data, metadata=metadata, mode=mode)

    # 4. HTML
    html_path = output_dir / "INSTRUKCJA.html"
    from vtd.builders.html_builder import render_html_manual
    render_html_manual(title=title, steps=steps, output_html=html_path, enriched_data=enriched_data, metadata=metadata, mode=mode)

    # 5. Prompt dla agenta
    prompt_text = build_llm_prompt(steps, module_title=title, mode=mode, metadata=metadata)
    # dopisek o adnotacjach
    if 'annotations_map' in locals() and annotations_map and any(annotations_map.values()):
        prompt_text += "\n\n[ADNOTACJE]: Klatki w frames_annotated/ zawierają adnotacje (strzałki, badge z numerami, highlight, dim). Odnieś się w opisie kroku do numerów na adnotacjach — np. '1 — Dodaj • 2 — Surowce' — i wyjaśnij każdy zaznaczony element. Numeracja badge odpowiada legendzie pod rysunkiem.\n"
    prompt_path = output_dir / "PROMPT_DLA_AGENTA.txt"
    prompt_path.write_text(prompt_text, encoding="utf-8")

    # 6. Manifest + podział internal / client-ready
    internal_dir = output_dir / "internal"
    client_ready_dir = output_dir / "client-ready"
    internal_dir.mkdir(parents=True, exist_ok=True)
    client_ready_dir.mkdir(parents=True, exist_ok=True)

    # Definicja co jest internal vs client-ready
    internal_files = ["PROMPT_DLA_AGENTA.txt", "QA_AUDIT.md", "ENRICHER_WARNING.md", "mic_isolated.wav", "client_isolated.wav", "COMPLETENESS_REPORT.md"]
    client_ready_files = ["INSTRUKCJA.md", "INSTRUKCJA.docx", "INSTRUKCJA.html", "TRANSKRYPCJA.md", "metadata.json", "COMPLETENESS_REPORT.md", "manifest.json"]

    manifest: Dict[str, Any] = {
        "title": title,
        "mode": mode,
        "document_status": metadata["document_status"],
        "generated_at": metadata["generated_at"],
        "internal": [],
        "client_ready": [],
        "client-ready": [],
        "artifacts": {},
        "note": "Draft DRAFT / DO WERYFIKACJI — materiał lokalny, nie wysłany do klienta. client-ready nie zawiera promptu, surowego WAV ani QA wewnetrznego. Nigdy nie oznaczaj draftu jako gotowy.",
        "stats": completeness,
    }
    # Kopiuj pliki do podkatalogów (jeśli istnieją)
    for fname in internal_files:
        src = output_dir / fname
        if src.exists():
            try:
                shutil.copy2(src, internal_dir / fname)
                manifest["internal"].append(fname)
                manifest["artifacts"][fname] = "internal"
            except Exception:
                pass
    for fname in client_ready_files:
        src = output_dir / fname
        if src.exists():
            # Dla client-ready pomijamy prompt/wav/QA — już zdefiniowane
            if fname in ("PROMPT_DLA_AGENTA.txt", "mic_isolated.wav", "client_isolated.wav", "QA_AUDIT.md"):
                continue
            try:
                if fname != "manifest.json":
                    shutil.copy2(src, client_ready_dir / fname)
                manifest["client_ready"].append(fname)
                manifest["client-ready"].append(fname)
                manifest["artifacts"][fname] = "client-ready"
            except Exception:
                pass
    # Dodaj frames do client-ready (dowody) — obie wersje gdy adnotacje
    manifest["artifacts"]["frames/"] = "client-ready"
    if frames_dir.exists():
        dest_frames = client_ready_dir / "frames"
        if not dest_frames.exists():
            try:
                shutil.copytree(frames_dir, dest_frames, dirs_exist_ok=True)
            except Exception:
                pass
        else:
            try:
                shutil.copytree(frames_dir, dest_frames, dirs_exist_ok=True)
            except Exception:
                pass
    # frames_annotated: internal + client-ready (główne obrazy kroków)
    if 'annotated_dir' in locals() and annotated_dir.exists() and any(annotated_dir.iterdir()):
        manifest["artifacts"]["frames_annotated/"] = "client-ready"
        # też internal dla traceability
        dest_ann = client_ready_dir / "frames_annotated"
        dest_ann_internal = internal_dir / "frames_annotated"
        try:
            shutil.copytree(annotated_dir, dest_ann, dirs_exist_ok=True)
        except Exception:
            pass
        try:
            shutil.copytree(annotated_dir, dest_ann_internal, dirs_exist_ok=True)
        except Exception:
            pass
        # also copy to root frames_annotated? already output_dir/frames_annotated exists
        # manifest lists
        manifest["client_ready"].append("frames_annotated/")
        manifest["client-ready"].append("frames_annotated/")
        manifest["artifacts"]["frames_annotated/"] = "both"
    # enrich manifest stats with annotations
    if 'annotations_per_step' in locals():
        manifest["annotations"] = annotations_per_step
        manifest["annotated_frames"] = annotated_frames_count if 'annotated_frames_count' in locals() else 0
    # frames_enhanced (nano banana): internal zawsze; client-ready TYLKO zaakceptowane *_accepted
    if 'nano_stats' in locals() and nano_stats.get("enabled"):
        manifest["nano_banana"] = {
            "processed": nano_stats.get("processed", 0),
            "accepted": nano_stats.get("accepted", 0),
            "rejected": nano_stats.get("rejected", 0),
            "cost_usd": round(nano_stats.get("cost_usd", 0.0), 4),
        }
        enh_dir = output_dir / "frames_enhanced"
        if enh_dir.exists():
            try:
                shutil.copytree(enh_dir, internal_dir / "frames_enhanced", dirs_exist_ok=True)
                manifest["internal"].append("frames_enhanced/")
                manifest["artifacts"]["frames_enhanced/"] = "internal"
            except Exception:
                pass
            accepted = [f for f in enh_dir.glob("*_accepted")]
            if accepted:
                dest_enh = client_ready_dir / "frames_enhanced"
                try:
                    dest_enh.mkdir(parents=True, exist_ok=True)
                    for f in enh_dir.iterdir():
                        if f.name.endswith("_accepted") or (enh_dir / (f.stem + "_accepted")).exists():
                            shutil.copy2(f, dest_enh / f.name)
                    manifest["client_ready"].append("frames_enhanced/")
                    manifest["client-ready"].append("frames_enhanced/")
                    manifest["artifacts"]["frames_enhanced/"] = "both"
                except Exception:
                    pass

    manifest_path = output_dir / "manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8")
    # Kopiuj manifest do client-ready
    try:
        shutil.copy2(manifest_path, client_ready_dir / "manifest.json")
    except Exception:
        pass

    print(f"\n[SUKCES] Zakończono przetwarzanie nagrania — DRAFT / DO WERYFIKACJI (nie wysłano do klienta)!")
    print(f"1. Dokument Word (.docx): {docx_path}")
    print(f"2. Interaktywny HTML: {html_path}")
    print(f"3. Czysta transkrypcja: {transcript_path}")
    print(f"4. Dokument Markdown: {doc_path}")
    print(f"5. Prompt LLM (internal): {prompt_path}")
    print(f"6. Katalog zrzutów ekranu: {frames_dir}")
    print(f"7. Metadane: {metadata_path}")
    print(f"8. Raport kompletności: {completeness_path}")
    print(f"9. Manifest: {manifest_path}")
    print(f"10. Podział: internal/ (wewnętrzne) vs client-ready/ (bezpieczny draft do weryfikacji)")
    print(f"   Status: {metadata['document_status']} — materiał lokalny, wymaga ręcznej weryfikacji przed wysyłką.")
    return steps


def main(argv=None):
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.command == "run":
        run_pipeline(
            video_path=Path(args.video_path),
            output_dir=Path(args.output),
            title=args.title,
            device=args.device,
            model_size=args.model,
            scene_threshold=args.scene_threshold,
            track=args.track,
            mode=args.mode,
            enrich=args.enrich,
            enrich_model=args.enrich_model,
            client=getattr(args, "client", None),
            process=getattr(args, "process", None),
            module=getattr(args, "module", None),
            environment=getattr(args, "environment", None),
            author=getattr(args, "author", None),
            document_status=getattr(args, "document_status", "DRAFT"),
            output_mode=getattr(args, "output_mode", "both"),
            max_step_seconds=getattr(args, "max_step_seconds", 45.0),
            grid_interval=getattr(args, "grid_interval", 30.0),
            frame_qa=getattr(args, "frame_qa", False),
            annotate=getattr(args, "annotate", False),
            nano_mode=getattr(args, "nano_mode", "located"),
            nano_banana=getattr(args, "nano_banana", False),
            nano_auto_accept=getattr(args, "nano_auto_accept", False),
            min_step_seconds=getattr(args, "min_step_seconds", 6.0),
        )
    elif args.command == "studio":
        import uvicorn
        from vtd.config import load_config
        cfg = load_config()
        host = args.host or cfg.server.host
        port = args.port or cfg.server.port
        uvicorn.run("vtd.studio.app:app", host=host, port=port, reload=False)
    elif args.command == "rerender":
        from vtd.core.rerender_docs import rerender_output_dir
        rerender_output_dir(Path(args.output_dir))
    else:
        parser.print_help()


if __name__ == "__main__":
    main()

