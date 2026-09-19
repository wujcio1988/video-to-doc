"""
audio_detection — auto-detekcja toru audio z najwyższym speech_score.

Mierzy per tor (map 0:a:i):
- mean_volume / max_volume via `ffmpeg -af volumedetect`
- speech_ratio via `silencedetect noise=-35dB:d=0.6` -> (duration - silence)/duration
Verdict: speech gdy ratio>=0.05 i mean>-50, weak gdy 0.01<=ratio<0.05, silent poniżej.
Best = max ratio wśród verdict != silent (tie-break niższy index).
Brak ffmpeg lub błędny plik -> zwraca warning, nie rzuca wyjątku.

Uwaga PATH pod systemd: jeśli `ffmpeg` nie znajdzie się przez shutil.which, fallback /usr/bin/ffmpeg.
Długie nagrania: ogranicz pomiar do pierwszych 120s (-t 120).
"""

import re
import shutil
import subprocess
from pathlib import Path
from typing import Optional


# mapowanie pipeline: audio stream index -> label flagi --track
_INDEX_TO_LABEL = {0: "mix", 1: "mic", 2: "2"}

# regex
_RE_MEAN = re.compile(r"mean_volume:\s*(-?\d+(?:\.\d+)?)\s*dB")
_RE_MAX = re.compile(r"max_volume:\s*(-?\d+(?:\.\d+)?)\s*dB")
_RE_NAN = re.compile(r"n/a", re.IGNORECASE)
# silencedetect
_RE_SIL_START = re.compile(r"silence_start:\s*([\d\.]+)")
_RE_SIL_END = re.compile(r"silence_end:\s*([\d\.]+)\s*\|\s*silence_duration:\s*([\d\.]+)")
# duration detection from ffmpeg stderr: Duration: HH:MM:SS.ms
_RE_DURATION = re.compile(r"Duration:\s*(\d+):(\d+):(\d+)\.(\d+)")


def _resolve_ffmpeg(ffmpeg_bin: Optional[str] = None) -> Optional[str]:
    """Zwraca ścieżkę do ffmpeg lub None jeśli brak."""
    if ffmpeg_bin:
        p = Path(ffmpeg_bin)
        if p.exists():
            return str(p)
        # try which
        w = shutil.which(ffmpeg_bin)
        if w:
            return w
        return None
    w = shutil.which("ffmpeg")
    if w:
        return w
    # fallback dla systemd PATH
    fallback = "/usr/bin/ffmpeg"
    if Path(fallback).exists():
        return fallback
    return None


def _resolve_ffprobe(ffmpeg_bin: Optional[str] = None) -> Optional[str]:
    """ffprobe obok ffmpeg, lub which."""
    if ffmpeg_bin and "/" in ffmpeg_bin:
        cand = str(Path(ffmpeg_bin).parent / "ffprobe")
        if Path(cand).exists():
            return cand
    w = shutil.which("ffprobe")
    if w:
        return w
    fallback = "/usr/bin/ffprobe"
    if Path(fallback).exists():
        return fallback
    return None


def _probe_duration(video_path: str, ffprobe_bin: Optional[str]) -> Optional[float]:
    """Zwraca duration wideo w sekundach, lub None."""
    # spróbuj ffprobe
    if ffprobe_bin:
        try:
            cmd = [ffprobe_bin, "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", video_path]
            r = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
            if r.returncode == 0:
                val = r.stdout.strip().splitlines()[0].strip() if r.stdout.strip() else ""
                if val and val != "N/A":
                    return float(val)
        except Exception:
            pass
    return None


def _measure_track(video_path: str, audio_index: int, ffmpeg_bin: str, duration: Optional[float]) -> dict:
    """
    Mierzy jeden tor audio. Zwraca dict {mean_volume_db, max_volume_db, speech_ratio, silence_total, duration_measured}.
    """
    # ograniczenie pomiaru do 120s jeśli plik długi
    use_limit = duration is not None and duration > 130
    # zbuduj komendę: ffmpeg -i V -t 120 -map 0:a:i -af volumedetect,silencedetect -f null -
    # łączymy oba filtry w jednym przebiegu dla wydajności
    af = "volumedetect,silencedetect=noise=-35dB:d=0.6"
    cmd = [ffmpeg_bin, "-i", video_path]
    if use_limit:
        cmd += ["-t", "120"]
    cmd += ["-map", f"0:a:{audio_index}", "-af", af, "-f", "null", "-"]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
        stderr = r.stderr or ""
    except subprocess.TimeoutExpired:
        return {"mean_volume_db": -91.0, "max_volume_db": -91.0, "speech_ratio": 0.0, "silence_total": 0.0, "duration_measured": 120.0 if use_limit else (duration or 0), "limited": use_limit}
    except Exception:
        return {"mean_volume_db": -91.0, "max_volume_db": -91.0, "speech_ratio": 0.0, "silence_total": 0.0, "duration_measured": 120.0 if use_limit else (duration or 0), "limited": use_limit}

    # parsuj volumedetect
    mean_db = None
    max_db = None
    m = _RE_MEAN.search(stderr)
    if m:
        try:
            mean_db = float(m.group(1))
        except Exception:
            mean_db = None
    # jeśli n/a (cisza absolutna) -> mean -91
    if mean_db is None:
        if _RE_NAN.search(stderr) and "mean_volume" in stderr:
            mean_db = -91.0
        else:
            mean_db = -91.0
    m2 = _RE_MAX.search(stderr)
    if m2:
        try:
            max_db = float(m2.group(1))
        except Exception:
            max_db = -91.0
    else:
        if _RE_NAN.search(stderr) and "max_volume" in stderr:
            max_db = -91.0
        else:
            max_db = mean_db if mean_db is not None else -91.0

    # parsuj silencedetect -> suma silence_duration
    silence_total = 0.0
    for line in stderr.splitlines():
        # silence_end zawiera duration
        em = _RE_SIL_END.search(line)
        if em:
            try:
                silence_total += float(em.group(2))
            except Exception:
                pass

    # duration: jeśli use_limit -> 120, inaczej użyj duration z ffprobe lub parsuj Duration z stderr
    if use_limit:
        dur = 120.0
    elif duration is not None and duration > 0:
        dur = duration
    else:
        dm = _RE_DURATION.search(stderr)
        if dm:
            try:
                h, m_, s, ms = dm.groups()
                dur = int(h) * 3600 + int(m_) * 60 + int(s) + int(ms) / 100.0
            except Exception:
                dur = 0.0
        else:
            dur = 0.0

    if dur <= 0:
        speech_ratio = 0.0
    else:
        # ogranicz silence_total do dur (czasem ffprobe vs silencedetect mismatch)
        if silence_total > dur:
            silence_total = dur
        speech_ratio = max(0.0, (dur - silence_total) / dur)
        # jeśli mean bardzo niskie (<-60) ale ratio wysokie (bo anullsrc nieskończona cisza nie wykrywana?), skoryguj?
        # Dla prawdziwej ciszy (mean -91, max -91) ratio może być 1.0 przez brak silence detekcji na samej ciszy -> traktuj jako 0
        # Sprawdzenie: ffmpeg volumedetect dla anullsrc daje mean -91, ale silencedetect może nie wykryć ciszy jeśli całkowita cisza
        # W praktyce silencedetect na anullsrc wykrywa silence_start 0 i silence_end dur, więc ratio ~0. Jeśli nie ma żadnego logu silence, a mean -91 -> ratio=0
        if mean_db is not None and mean_db <= -60 and speech_ratio > 0.05:
            # jeśli nie było żadnego silence_start, to znaczy cały plik to cisza -> zeruj
            has_silence = "silence_start" in stderr
            if not has_silence:
                speech_ratio = 0.0

    return {
        "mean_volume_db": float(mean_db),
        "max_volume_db": float(max_db),
        "speech_ratio": float(round(speech_ratio, 4)),
        "silence_total": float(silence_total),
        "duration_measured": float(dur),
        "limited": use_limit,
    }


def _get_n_streams(video_path: str, ffprobe_bin: Optional[str], ffmpeg_bin: Optional[str]) -> int:
    """Zwraca liczbę strumieni audio, lub 0 przy błędzie."""
    if ffprobe_bin:
        try:
            cmd = [ffprobe_bin, "-v", "error", "-select_streams", "a", "-show_entries", "stream=index", "-of", "csv=p=0", video_path]
            r = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
            if r.returncode == 0:
                return len([l for l in r.stdout.splitlines() if l.strip()])
            # fallback: ffmpeg stderr parsing?
        except Exception:
            pass
    # fallback: parsuj ffmpeg -i
    if ffmpeg_bin:
        try:
            cmd = [ffmpeg_bin, "-i", video_path, "-f", "null", "-"]
            r = subprocess.run(cmd, capture_output=True, text=True, timeout=15)
            stderr = r.stderr or ""
            # licz linie "Stream #0:.*Audio"
            cnt = len(re.findall(r"Stream #\d+:\d+.*Audio", stderr))
            if cnt > 0:
                return cnt
        except Exception:
            pass
    return 0


def detect_best_track(video_path: str, ffmpeg_bin: Optional[str] = None) -> dict:
    """
    Auto-detekcja najlepszego toru audio.

    Mierzy każdy tor audio (0:a:i) pod kątem głośności i udziału mowy.
    Zwraca słownik:

    {
        "n_streams": int,
        "best_track_index": int | None,
        "best_track_label": str,        # "mic"|"mix"|"2"
        "tracks": [ {index, mean_volume_db, max_volume_db, speech_ratio, verdict} ],
        "has_speech": bool,
        "warning": str | None,
    }

    Odporność: brak ffmpeg lub błędny plik -> warning PL, has_speech=False lub None semantics
              (has_speech=False z warning), nigdy nie rzuca wyjątku.
    """
    ffmpeg = _resolve_ffmpeg(ffmpeg_bin)
    ffprobe = _resolve_ffprobe(ffmpeg_bin or ffmpeg)

    if not ffmpeg:
        return {
            "n_streams": 0,
            "best_track_index": None,
            "best_track_label": "mic",
            "tracks": [],
            "has_speech": None,
            "warning": "nie można zmierzyć — brak ffmpeg w PATH, użyj track ręcznie",
            "limited_to_120s": False,
        }

    # sprawdź istnienie pliku
    p = Path(video_path)
    if not p.exists() or not p.is_file():
        return {
            "n_streams": 0,
            "best_track_index": None,
            "best_track_label": "mic",
            "tracks": [],
            "has_speech": None,
            "warning": "nie można zmierzyć — plik nie istnieje lub błędny, użyj track ręcznie",
            "limited_to_120s": False,
        }

    duration = _probe_duration(video_path, ffprobe)
    n_streams = _get_n_streams(video_path, ffprobe, ffmpeg)

    if n_streams == 0:
        # brak streamów audio (lub błąd odczytu) -> warning
        return {
            "n_streams": 0,
            "best_track_index": None,
            "best_track_label": "mic",
            "tracks": [],
            "has_speech": None,
            "warning": "nie można zmierzyć — brak strumieni audio lub błąd pliku, użyj track ręcznie",
            "limited_to_120s": False,
        }

    tracks = []
    limited_any = False
    for i in range(n_streams):
        meas = _measure_track(video_path, i, ffmpeg, duration)
        if meas.get("limited"):
            limited_any = True
        mean_db = meas["mean_volume_db"]
        ratio = meas["speech_ratio"]
        max_db = meas["max_volume_db"]
        # verdict
        if ratio >= 0.05 and mean_db > -50:
            verdict = "speech"
        elif 0.01 <= ratio < 0.05:
            # weak jeśli ma choć trochę mowy, nawet z ciszą? Tak per spec
            verdict = "weak"
        else:
            # dodatkowo jeśli ratio>=0.05 ale mean <=-50 to weak? spec mówi tylko >=0.05 i mean>-50 -> speech, inaczej patrz ratio
            # ale jeśli ratio 0.06 i mean -55 to powinien być weak/silent? Ratio weak obejmuje 0.01-0.05, więc 0.06 nie łapie -> silent mimo że ratio wysoki
            # Spec: verdict speech gdy ratio>=0.05 i mean>-50; weak gdy 0.01<=ratio<0.05; silent poniżej.
            # Jeśli ratio>=0.05 ale mean<=-50 to nie speech ani weak -> silent per spec. Tak zostawiamy.
            # Jednak dla bezpieczeństwa: jeśli ratio>=0.05 ale mean<=-50 -> silent (bo cichy tor nie jest mową)
            verdict = "silent"
            # Jeśli ratio 0.06 i mean -55 -> silent, ale czy to poprawne? Tak, bo mean wskazuje ciszę.
            # Jeśli chcemy rozróżnić weak gdy ratio wysokie ale głośność niska, spec tego nie definiuje.
            # Zostawiamy silent.

        tracks.append({
            "index": i,
            "mean_volume_db": round(float(mean_db), 1),
            "max_volume_db": round(float(max_db), 1),
            "speech_ratio": float(ratio),
            "verdict": verdict,
            "duration_measured": round(float(meas["duration_measured"]), 1),
        })

    # best = max speech_ratio wśród verdict != silent, tie-break niższy index
    candidates = [t for t in tracks if t["verdict"] != "silent"]
    if candidates:
        # sort by -ratio, index
        candidates.sort(key=lambda t: (-t["speech_ratio"], t["index"]))
        best = candidates[0]
        best_index = best["index"]
        has_speech = any(t["verdict"] == "speech" for t in tracks)
    else:
        # wszyscy silent -> brak best, has_speech False
        # wybierz tor z max ratio (nawet silent) jako best do raportu? Spec: best_track_index = None gdy brak?
        # Spec mówi best_track_index int|None — gdy brak mowy, None. Zostawiamy None.
        best_index = None
        has_speech = False

    if best_index is None:
        # fallback label mic (jak w pipeline gdy n_streams<2)
        best_label = "mic"
    else:
        best_label = _INDEX_TO_LABEL.get(best_index, str(best_index))

    # warning PL
    warning = None
    if not has_speech:
        if has_speech is False:
            warning = "Brak wykrywalnej narracji w nagraniu — dokument powstanie tylko z OCR klatek"
        else:
            # None case already handled
            pass
    # has_speech None semantyka tylko dla brak ffmpeg / błąd pliku (wyżej return)
    # Tu has_speech jest bool

    # Jeśli pomiar ograniczony do 120s, zaznacz w warning/raporcie
    # nie nadpisuj warning jeśli już jest, ale dodaj info przez pole limited_to_120s

    result = {
        "n_streams": n_streams,
        "best_track_index": best_index,
        "best_track_label": best_label,
        "tracks": tracks,
        "has_speech": has_speech,
        "warning": warning,
        "limited_to_120s": limited_any,
    }
    return result
