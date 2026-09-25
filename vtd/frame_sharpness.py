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


def select_representative_frames(
    frames: Sequence,
    video_duration: Optional[float] = None,
    max_frames: int = 40,
) -> List:
    """Wybiera ograniczony, równomierny zestaw klatek dla trybu meeting.

    Zachowuje pokrycie całej osi czasu, a w każdym przedziale wybiera najostrzejszą
    klatkę. Nie używa LLM: wynik jest deterministyczny i tani. Dla manualu nie
    należy stosować tej funkcji, bo manual wymaga wszystkich zmian ekranu.
    """
    items = [f for f in frames if getattr(f, "path", None) and Path(f.path).exists()]
    if max_frames <= 0 or len(items) <= max_frames:
        return list(items)
    if video_duration is None or video_duration <= 0:
        video_duration = max(float(getattr(f, "timestamp", 0.0)) for f in items) + 1.0
    # Przedziały czasowe gwarantują, że nie wybierzemy wyłącznie klatek z jednego tematu.
    buckets = [[] for _ in range(max_frames)]
    for frame in items:
        ratio = min(0.999999, max(0.0, float(frame.timestamp) / video_duration))
        buckets[min(max_frames - 1, int(ratio * max_frames))].append(frame)
    selected = []
    for bucket in buckets:
        if bucket:
            ranked = rank_sharp_frames([f.path for f in bucket])
            by_path = {Path(f.path): f for f in bucket}
            selected.append(by_path[ranked[0][1]] if ranked else bucket[0])
    # Jeśli puste bucket'y dały mniej niż limit, dobierz kolejne najlepsze globalnie.
    chosen_paths = {Path(f.path) for f in selected}
    remaining = [f for f in items if Path(f.path) not in chosen_paths]
    for _, path in rank_sharp_frames([f.path for f in remaining]):
        if len(selected) >= max_frames:
            break
        selected.append({Path(f.path): f for f in remaining}[path])
    return sorted(selected, key=lambda f: float(f.timestamp))


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
        from vtd.frame_extractor import extract_frame_at as extract_fn
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
