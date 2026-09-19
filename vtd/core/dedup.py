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
    min_time_delta: float = 1.5
) -> List[ExtractedFrame]:
    """
    Eliminuje klatki o wysokim podobieństwie percepcyjnym (pHash Hamming distance <= threshold)
    oraz klatki następujące zbyt gęsto po sobie (< min_time_delta).
    """
    if not frames:
        return []
    
    try:
        from PIL import Image
        import imagehash
    except ImportError:
        return frames

    unique_frames = [frames[0]]
    prev_hash = imagehash.phash(Image.open(frames[0].path))
    
    for frame in frames[1:]:
        time_diff = abs(frame.timestamp - unique_frames[-1].timestamp)
        if time_diff < min_time_delta:
            continue
            
        curr_hash = imagehash.phash(Image.open(frame.path))
        dist = curr_hash - prev_hash
        if dist > hamming_threshold:
            unique_frames.append(frame)
            prev_hash = curr_hash
            
    return unique_frames
