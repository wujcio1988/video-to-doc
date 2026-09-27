from dataclasses import dataclass
from pathlib import Path
from typing import List

@dataclass
class ExtractedFrame:
    timestamp: float
    path: Path

def filter_duplicate_frames(
    frames: List[ExtractedFrame],
    hamming_threshold: int = 8,
    min_time_delta: float = 1.5,
    compare_window: int = 5,
) -> List[ExtractedFrame]:
    """
    Eliminuje klatki o wysokim podobieństwie percepcyjnym (pHash Hamming distance <= threshold)
    oraz klatki następujące zbyt gęsto po sobie (< min_time_delta).

    GF-011: brak bibliotek (PIL/imagehash) NIE wyłącza już dedupu po cichu —
    wypisywane jest jawne ostrzeżenie, a porównanie idzie względem okna ostatnich
    N zaakceptowanych hashy (okno chroni przed powolnym dryfem obrazu).
    """
    if not frames:
        return []

    try:
        from PIL import Image
        import imagehash
    except ImportError as e:
        print(f"OSTRZEŻENIE: deduplikacja percepcyjna (pHash) WYŁĄCZONA — brak zależności: {e}. "
              f"Wynik może zawierać zduplikowane klatki. Zainstaluj: pip install imagehash pillow")
        return frames

    from collections import deque
    recent_hashes = deque(maxlen=max(1, compare_window))
    recent_hashes.append(imagehash.phash(Image.open(frames[0].path)))
    unique_frames = [frames[0]]

    for frame in frames[1:]:
        time_diff = abs(frame.timestamp - unique_frames[-1].timestamp)
        if time_diff < min_time_delta:
            continue

        curr_hash = imagehash.phash(Image.open(frame.path))
        # porównanie z oknem ostatnich N hashy — powolny dryf nie przepuszcza duplikatów
        min_dist = min(curr_hash - h for h in recent_hashes)
        if min_dist > hamming_threshold:
            unique_frames.append(frame)
            recent_hashes.append(curr_hash)

    return unique_frames
