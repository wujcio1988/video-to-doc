"""Hierarchia źródeł klatki dla rendererów: enhanced -> annotated -> original.

Nano banana (frames_enhanced/) ma priorytet, gdy plik _accepted istnieje — oznacza,
że użytkownik zaakceptował wersję AI w podglądzie Studio (albo auto-akceptacja
przy braku walidacji wprost z flagi). Bez akceptacji renderery biorą wersję
adnotowaną (deterministyczną) albo oryginał.
"""
from pathlib import Path

ENHANCED_DIR = "frames_enhanced"
ANNOTATED_DIR = "frames_annotated"


def resolve_frame(base_dir, frame_name: str, require_accepted: bool = True):
    """Zwraca ścieżkę klatki wg priorytetu lub None gdy base_dir nieznany."""
    if base_dir is None:
        return None
    base_dir = Path(base_dir)
    enh = base_dir / ENHANCED_DIR / frame_name
    if enh.exists():
        if not require_accepted:
            return enh
        if (base_dir / ENHANCED_DIR / (Path(frame_name).stem + "_accepted")).exists():
            return enh
    ann = base_dir / ANNOTATED_DIR / frame_name
    if ann.exists():
        return ann
    orig = base_dir / "frames" / frame_name
    if orig.exists():
        return orig
    return None
