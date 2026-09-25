"""Pure, fail-closed selection of semantically supported evidence frames."""
from typing import Any, Dict, Iterable, Optional


def validate_frame_candidate(candidate: Any, interval: Optional[tuple[float, float]] = None) -> bool:
    if not isinstance(candidate, dict):
        return False
    if interval is not None:
        timestamp = candidate.get("timestamp")
        if not isinstance(timestamp, (int, float)) or not (interval[0] <= timestamp <= interval[1]):
            return False
    if str(candidate.get("qa_verdict", "")).lower() != "ok":
        return False
    relation = candidate.get("ocr_relation") is True or candidate.get("semantic_relation") is True
    if not relation:
        return False
    if str(candidate.get("position", "")).lower() in {"mid", "midpoint", "middle"}:
        return False
    return True


def select_evidence_frame(candidates: Iterable[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """Return strongest accepted after/before frame; never midpoint by default."""
    accepted = [c for c in candidates if validate_frame_candidate(c)]
    if not accepted:
        return None
    order = {"after": 0, "before": 1, "result": 0}
    accepted.sort(key=lambda c: (order.get(str(c.get("position", "")).lower(), 2), -float(c.get("score", 0) or 0)))
    return accepted[0]
