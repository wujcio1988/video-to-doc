import re
import subprocess
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional

# VTD-011: limity czasu dla ffmpeg/ffprobe (ffprobe krótkie; operacje na całym
# nośniku mają limit skalowany z długością, min. 120 s, max. 2 h)
FFPROBE_TIMEOUT = 30.0
FFMPEG_TIMEOUT_MIN = 120.0
FFMPEG_TIMEOUT_MAX = 2 * 60 * 60


def probe_media_duration(media_path: Path) -> Optional[float]:
    """Zwraca długość nośnika w sekundach lub None jeśli nie udało się ustalić."""
    cmd = [
        "ffprobe", "-v", "error",
        "-show_entries", "format=duration",
        "-of", "default=noprint_wrappers=1:nokey=1",
        str(media_path)
    ]
    try:
        res = subprocess.run(cmd, capture_output=True, text=True, timeout=FFPROBE_TIMEOUT)
        return float(res.stdout.strip())
    except Exception:
        return None


def media_timeout(media_path: Path, speed_factor: float = 2.0) -> float:
    """Rozsądny limit czasu dla operacji na całym nośniku (VTD-011).

    Skaluje się z długością materiału: min. 120 s (zgodnie z audytem), max. 2 h.
    """
    duration = probe_media_duration(media_path)
    if duration is None or duration <= 0:
        return FFMPEG_TIMEOUT_MAX
    return max(FFMPEG_TIMEOUT_MIN, min(FFMPEG_TIMEOUT_MAX, duration * speed_factor))

@dataclass
class SilenceInterval:
    start: float
    end: float
    duration: float

def parse_silence_log(log_output: str) -> List[SilenceInterval]:
    """
    Parsuje wyjście filtra silencedetect z biblioteki FFmpeg.
    """
    intervals = []
    current_start = None
    start_pattern = re.compile(r"silence_start:\s*([\d\.]+)")
    end_pattern = re.compile(r"silence_end:\s*([\d\.]+)\s*\|\s*silence_duration:\s*([\d\.]+)")
    
    for line in log_output.splitlines():
        start_match = start_pattern.search(line)
        if start_match:
            current_start = float(start_match.group(1))
        end_match = end_pattern.search(line)
        if end_match and current_start is not None:
            end = float(end_match.group(1))
            duration = float(end_match.group(2))
            intervals.append(SilenceInterval(start=current_start, end=end, duration=duration))
            current_start = None
    return intervals

def check_ffmpeg_available():
    if not shutil.which("ffmpeg"):
        raise EnvironmentError(
            "Brak programu 'ffmpeg' w systemie! "
            "Zainstaluj go poleceniem: sudo apt update && sudo apt install -y ffmpeg"
        )

def get_audio_stream_count(video_path: Path) -> int:
    """
    Zwraca liczbę ścieżek audio dostępnych w kontenerze wideo.
    """
    check_ffmpeg_available()
    cmd = [
        "ffprobe", "-v", "error",
        "-select_streams", "a",
        "-show_entries", "stream=index",
        "-of", "csv=p=0",
        str(video_path)
    ]
    try:
        res = subprocess.run(cmd, capture_output=True, text=True, timeout=FFPROBE_TIMEOUT)
    except subprocess.TimeoutExpired as e:
        raise RuntimeError(f"ffprobe przekroczył limit czasu ({FFPROBE_TIMEOUT:.0f}s) dla: {video_path}") from e
    return len([line for line in res.stdout.splitlines() if line.strip()])

def extract_mic_audio(video_path: Path, output_wav: Path, track_index: int = 1) -> Path:
    """
    Wyciąga wyizolowaną ścieżkę mikrofonu (Track 2 w OBS -> 0:a:1) do 16kHz WAV mono.
    """
    check_ffmpeg_available()
    output_wav.parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        "ffmpeg", "-y",
        "-i", str(video_path),
        "-map", f"0:a:{track_index}",
        "-ac", "1",
        "-ar", "16000",
        str(output_wav)
    ]
    res = None
    try:
        res = subprocess.run(cmd, capture_output=True, text=True, timeout=media_timeout(video_path))
    except subprocess.TimeoutExpired as e:
        raise RuntimeError(f"Ekstrakcja audio przekroczyła limit czasu dla: {video_path}") from e
    if res.returncode != 0:
        raise RuntimeError(f"Błąd ekstrakcji audio przez FFmpeg: {res.stderr}")
    return output_wav

def detect_silence(audio_wav: Path, noise_db: float = -40.0, min_duration: float = 1.2) -> List[SilenceInterval]:
    """
    Wykrywa pauzy w mowie lektora w wyizolowanym pliku audio.
    """
    check_ffmpeg_available()
    cmd = [
        "ffmpeg",
        "-i", str(audio_wav),
        "-af", f"silencedetect=noise={noise_db}dB:d={min_duration}",
        "-f", "null", "-"
    ]
    try:
        res = subprocess.run(cmd, capture_output=True, text=True, timeout=media_timeout(audio_wav))
    except subprocess.TimeoutExpired as e:
        raise RuntimeError(f"Wykrywanie ciszy przekroczyło limit czasu dla: {audio_wav}") from e
    return parse_silence_log(res.stderr)
