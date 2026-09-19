"""Pre-selekcja ostrości kandydatów klatek (P3, Etap dostrajania jakości).

Wzorzec przejęty z PerfectFrameAI (NIMA) — ocena jakości klatki PRZED kosztem LLM —
ale implementacja deterministyczna: Laplacian variance (klasyczna miara ostrości),
numpy-only (bez OpenCV/CUDA). Klatka rozmyta = niski score; ostra = wysoki.
"""
from pathlib import Path
from typing import List, Optional, Sequence, Tuple

import numpy as np

# Downscale do tej szerokości przed scorem: stabilizuje porównania między
# klatkami w różnych rozdzielczościach i przyspiesza konwolucję.
_ANALYSIS_WIDTH = 640

# Rdzeń 3x3 Laplace (wariant z ujemną sumą środkową, bez przekątnych).
_LAPLACE_KERNEL = np.array(
    [
        [0.0, -1.0, 0.0],
        [-1.0, 4.0, -1.0],
        [0.0, -1.0, 0.0],
    ],
    dtype=np.float64,
)


def _load_gray_small(path, width: int = _ANALYSIS_WIDTH) -> Optional[np.ndarray]:
    """Wczytuje obraz, skala szarości, downscale do szerokości `width`. None przy błędzie."""
    try:
        from PIL import Image

        img = Image.open(path).convert("L")
        if img.width > width:
            ratio = width / img.width
            img = img.resize((width, max(1, int(img.height * ratio))))
        return np.asarray(img, dtype=np.float64)
    except Exception:
        return None


def _laplacian(gray: np.ndarray) -> np.ndarray:
    """Konwolucja 3x3 Laplace na tablicy 2D (numpy-only, via stride tricks)."""
    h, w = gray.shape
    if h < 3 or w < 3:
        return np.zeros_like(gray)
    padded = np.pad(gray, 1, mode="edge")
    windows = np.lib.stride_tricks.sliding_window_view(padded, (3, 3))
    return np.einsum("xy,ijxy->ij", _LAPLACE_KERNEL, windows)


def sharpness_score(path) -> float:
    """Laplacian variance: 0.0 dla pliku nieczytelnego; większy = ostrzejsza klatka."""
    gray = _load_gray_small(path)
    if gray is None or gray.size == 0:
        return 0.0
    lap = _laplacian(gray)
    return float(lap.var())


def rank_sharp_frames(paths: Sequence) -> List[Tuple[float, Path]]:
    """Sortuje klatki malejąco po ostrości; pliki nieczytelne (score 0) na końcu.
    Zwraca listę (score, path)."""
    scored: List[Tuple[float, Path]] = []
    for p in paths:
        p = Path(p)
        if not p.exists() or p.stat().st_size == 0:
            continue
        scored.append((sharpness_score(p), p))
    scored.sort(key=lambda t: t[0], reverse=True)
    return scored


def best_sharp_frame(paths: Sequence) -> Optional[Path]:
    """Najostrzejsza klatka lub None (gdy brak czytelnych plików)."""
    ranked = rank_sharp_frames(paths)
    return ranked[0][1] if ranked else None


def extract_sharp_candidates(
    video_path,
    start_time: float,
    end_time: float,
    out_dir,
    step_number: int,
    k: int = 4,
    extract_fn=None,
) -> List:
    """Wycina k kandydatów równomiernie z okna [15%..90%] kroku (pomija krańce,
    gdzie bywają przejścia ekranu). Zwraca listę ścieżek (tylko te, które się
    poprawnie wycięły). `extract_fn` do wstrzyknięcia w testach."""
    if extract_fn is None:
        from vtd.core.frame_extractor import extract_frame_at as extract_fn
    duration = float(end_time) - float(start_time)
    if duration <= 0 or k < 1:
        return []
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    lo = start_time + duration * 0.15
    hi = start_time + duration * 0.90
    step_len = (hi - lo) / k if k > 1 else 0.0
    paths: List = []
    for i in range(k):
        t = lo + step_len * i if k > 1 else lo
        out_path = out_dir / f"grid_cand_{step_number:04d}_{i:02d}.png"
        try:
            res = extract_fn(video_path, t, out_path)
        except Exception:
            res = None
        if res is not None and Path(res).exists() and Path(res).stat().st_size > 0:
            paths.append(Path(res))
    return paths
