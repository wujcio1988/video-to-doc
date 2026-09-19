import re
import subprocess
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import List

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
    res = subprocess.run(cmd, capture_output=True, text=True)
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
    res = subprocess.run(cmd, capture_output=True, text=True)
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
    res = subprocess.run(cmd, capture_output=True, text=True)
    return parse_silence_log(res.stderr)
