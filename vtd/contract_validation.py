"""Pure validation for MANUAL and MEETING contract payloads."""
from dataclasses import asdict, is_dataclass
from html import escape
from typing import Any, Callable, Iterable, Mapping, Optional, Tuple

from .contracts import ExistsCallback

_MANUAL_FIELDS = ("action", "ui_target", "value", "expected_result", "condition_warning",
                  "time_range", "evidence", "qualification", "status")
_FINAL = "FINAL"
_BAD_QA = {"wrong", "unclear", "unknown", "not_reviewed"}


def _mapping(value: Any) -> Mapping[str, Any]:
    return asdict(value) if is_dataclass(value) else value


def escape_html(value: str) -> str:
    return escape(str(value), quote=True)


def validate_no_final_status(document: Any) -> list[str]:
    errors = []
    data = _mapping(document)
    if isinstance(data, Mapping) and str(data.get("status", "")).upper() == _FINAL:
        errors.append("status FINAL is not allowed")
    return errors


def validate_evidence_alignment(evidence: Iterable[Any], interval: Optional[Tuple[float, float]] = None,
                                exists: Optional[ExistsCallback] = None) -> list[str]:
    errors = []
    exists = exists or (lambda path: False)
    for index, raw in enumerate(evidence):
        item = _mapping(raw)
        prefix = f"evidence[{index}]"
        if not isinstance(item, Mapping):
            errors.append(f"{prefix} must be an object"); continue
        path = item.get("frame_path", item.get("frame"))
        if not path or not exists(str(path)):
            errors.append(f"{prefix} file does not exist")
        timestamp = item.get("timestamp")
        if not isinstance(timestamp, (int, float)):
            errors.append(f"{prefix} timestamp is invalid")
        elif interval is not None and not (interval[0] <= timestamp <= interval[1]):
            errors.append(f"{prefix} timestamp is outside interval")
        if item.get("frame_qa") != "ok":
            errors.append(f"{prefix} frame_qa must be ok")
        if item.get("semantic_match") is not True and item.get("ocr_match") is not True:
            errors.append(f"{prefix} semantic/OCR match is not confirmed")
    return errors


def validate_manual_document(document: Any, exists: Optional[ExistsCallback] = None) -> list[str]:
    data = _mapping(document); errors = validate_no_final_status(document)
    if not isinstance(data, Mapping): return ["manual document must be an object"]
    for field in ("title", "steps"):
        if field not in data: errors.append(f"missing {field}")
    if data.get("completeness") not in (None, "COMPLETE", "PARTIAL", "UNKNOWN"):
        errors.append("completeness must be COMPLETE, PARTIAL, or UNKNOWN")
    if data.get("completeness") == "COMPLETE" and not data.get("goal"):
        errors.append("complete manual must declare a goal")
    for i, raw in enumerate(data.get("steps", [])):
        step = _mapping(raw)
        if not isinstance(step, Mapping): errors.append(f"steps[{i}] must be an object"); continue
        for field in _MANUAL_FIELDS:
            if field not in step: errors.append(f"steps[{i}] missing {field}")
        interval = step.get("time_range")
        if isinstance(interval, (list, tuple)) and len(interval) == 2:
            errors.extend(f"steps[{i}]: {e}" for e in validate_evidence_alignment(step.get("evidence", []), tuple(interval), exists))
        else: errors.append(f"steps[{i}] time_range is invalid")
        if str(step.get("status", "")).upper() == _FINAL: errors.append("status FINAL is not allowed")
    return errors


def validate_meeting_document(document: Any, exists: Optional[ExistsCallback] = None) -> list[str]:
    data = _mapping(document); errors = validate_no_final_status(document)
    if not isinstance(data, Mapping): return ["meeting document must be an object"]
    for field in ("decisions", "agreements", "action_items", "open_questions", "risks", "participants", "evidence"):
        if field not in data: errors.append(f"missing {field}")
    for i, participant in enumerate(data.get("participants", [])):
        p = _mapping(participant)
        for field in ("speaker_label", "display_name", "identity_status"):
            if not isinstance(p, Mapping) or not p.get(field): errors.append(f"participants[{i}] missing {field}")
        if isinstance(p, Mapping) and p.get("identity_status") not in ("explicit", "mentioned_only"):
            errors.append(f"participants[{i}] identity must be explicit or mentioned_only")
    for i, item in enumerate(data.get("action_items", [])):
        a = _mapping(item)
        for field in ("owner_status", "due_status"):
            if not isinstance(a, Mapping) or field not in a: errors.append(f"action_items[{i}] missing {field}")
    errors.extend(validate_evidence_alignment(data.get("evidence", []), exists=exists))
    return errors
