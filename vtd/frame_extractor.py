import re
import subprocess
import shutil
from pathlib import Path
from typing import List, Optional
from vtd.dedup import ExtractedFrame

def check_ffmpeg_available():
    if not shutil.which("ffmpeg"):
        raise EnvironmentError(
            "Brak programu 'ffmpeg' w systemie! Zainstaluj go: sudo apt update && sudo apt install -y ffmpeg"
        )

def extract_scene_frames(
    video_path: Path,
    output_dir: Path,
    threshold: float = 0.35,
    scale_width: int = 1600
) -> List[ExtractedFrame]:
    """
    Wycina klatki wideo w momentach wyraźnych zmian scen (okien / formularzy / dialogów).
    """
    check_ffmpeg_available()
    output_dir.mkdir(parents=True, exist_ok=True)
    out_pattern = str(output_dir / "scene_%04d.png")
    
    cmd = [
        "ffmpeg", "-y",
        "-i", str(video_path),
        "-vf", f"select='eq(n\\,0)+gt(scene,{threshold})',scale={scale_width}:-1,showinfo",
        "-fps_mode", "vfr",
        out_pattern
    ]
    res = subprocess.run(cmd, capture_output=True, text=True)
    if res.returncode != 0:
        raise RuntimeError(f"FFmpeg scene extraction failed (code {res.returncode}): {res.stderr[-1200:]}")
    
    pts_times = []
    pts_pattern = re.compile(r"pts_time:\s*([\d\.]+)")
    for line in res.stderr.splitlines():
        if "showinfo" in line:
            m = pts_pattern.search(line)
            if m:
                pts_times.append(float(m.group(1)))
                
    files = sorted(list(output_dir.glob("scene_*.png")))
    if not files:
        raise RuntimeError("FFmpeg nie wyodrębnił żadnej klatki (brak dowodu obrazu).")
    if len(pts_times) != len(files):
        raise RuntimeError(
            f"Niespójna ekstrakcja klatek: pliki={len(files)}, timestampy={len(pts_times)}; "
            "nie przypisuję zastępczych czasów."
        )
    return [ExtractedFrame(timestamp=t, path=p) for t, p in zip(pts_times, files)]


def extract_frame_at(
    video_path: Path,
    t_seconds: float,
    out_path: Path,
    scale_width: int = 1600,
) -> Optional[Path]:
    """Wycina jedną klatkę w czasie t_seconds (ffmpeg -ss t -frames:v 1). Zwraca ścieżkę lub None przy błędzie."""
    try:
        check_ffmpeg_available()
        if t_seconds < 0:
            t_seconds = 0.0
        out_path.parent.mkdir(parents=True, exist_ok=True)
        cmd = [
            "ffmpeg", "-y",
            "-ss", f"{t_seconds:.3f}",
            "-i", str(video_path),
            "-frames:v", "1",
            "-vf", f"scale={scale_width}:-1",
            str(out_path),
        ]
        res = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        if res.returncode != 0 or not out_path.exists() or out_path.stat().st_size == 0:
            if out_path.exists():
                try:
                    out_path.unlink()
                except Exception:
                    pass
            print(f"[FRAME_EXTRACTOR] Ostrzeżenie: nie udało się wyciąć klatki w {t_seconds:.2f}s (code {res.returncode})")
            return None
        return out_path
    except Exception as e:
        print(f"[FRAME_EXTRACTOR] Ostrzeżenie: błąd extract_frame_at {t_seconds:.2f}s: {e}")
        return None
