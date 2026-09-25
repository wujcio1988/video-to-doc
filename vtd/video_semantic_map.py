"""Optional Google Gemini semantic video map; never replaces local QA."""
from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

STATUSES = {"DISABLED", "UNAVAILABLE", "PARTIAL", "COMPLETE"}
_REQUIRED = ("start", "end", "type", "description", "spoken_evidence", "visible_evidence", "confidence", "needs_verification")


def _json_payload_from_response(response: Any) -> Optional[Dict[str, Any]]:
    """Extract supported Gemini v2 function-call and JSON response shapes."""
    calls = list(getattr(response, "function_calls", None) or [])
    for candidate in getattr(response, "candidates", None) or []:
        content = candidate.get("content") if isinstance(candidate, dict) else getattr(candidate, "content", None)
        parts = content.get("parts") if isinstance(content, dict) else getattr(content, "parts", None)
        for part in parts or []:
            call = part.get("function_call") if isinstance(part, dict) else getattr(part, "function_call", None)
            if call is not None:
                calls.append(call)
    for call in calls:
        name = call.get("name") if isinstance(call, dict) else getattr(call, "name", None)
        if name not in {"set_timecodes", "set_timecodes_with_objects", "set_timecodes_with_numeric_values"}:
            continue
        args = call.get("args") if isinstance(call, dict) else getattr(call, "args", None)
        if isinstance(args, str):
            try:
                args = json.loads(args)
            except (TypeError, ValueError):
                continue
        if isinstance(args, dict) and isinstance(args.get("items"), list):
            return args
    text = getattr(response, "text", "")
    if not isinstance(text, str) or not text.strip():
        return None
    text = text.strip()
    if text.startswith("```"):
        lines = text.splitlines()
        if len(lines) < 3 or not lines[-1].strip().startswith("```"):
            return None
        text = "\n".join(lines[1:-1]).strip()
    try:
        payload = json.loads(text)
    except (TypeError, ValueError):
        return None
    if isinstance(payload, dict):
        return payload
    # Gemini JSON mode may emit the item array directly; normalize it while
    # keeping validation strict for each candidate below.
    return {"items": payload} if isinstance(payload, list) else None


def validate_semantic_map_response(response: Any) -> "SemanticVideoMapResult":
    payload = _json_payload_from_response(response)
    return validate_semantic_map(payload) if payload is not None else SemanticVideoMapResult(
        "UNAVAILABLE", [], "invalid structured response"
    )


def _timestamp_seconds(value: Any) -> float:
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        text = value.strip()
        if ":" in text:
            parts = text.split(":")
            if len(parts) == 2:
                minutes, seconds = parts
                return float(minutes) * 60.0 + float(seconds)
            if len(parts) == 3:
                hours, minutes, seconds = parts
                return float(hours) * 3600.0 + float(minutes) * 60.0 + float(seconds)
        return float(text)
    raise ValueError("invalid timestamp")


def validate_semantic_map(payload: Any) -> "SemanticVideoMapResult":
    if not isinstance(payload, dict) or not isinstance(payload.get("items"), list):
        return SemanticVideoMapResult("UNAVAILABLE", [], "invalid structured response")
    items: List[Dict[str, Any]] = []
    for raw in payload["items"]:
        if not isinstance(raw, dict) or any(key not in raw for key in _REQUIRED):
            continue
        try:
            start, end = _timestamp_seconds(raw["start"]), _timestamp_seconds(raw["end"])
            confidence = float(raw["confidence"])
        except (TypeError, ValueError):
            continue
        if start < 0 or end < start or not 0 <= confidence <= 1:
            continue
        item = {key: raw[key] for key in _REQUIRED}
        item.update(start=start, end=end, confidence=confidence, needs_verification=bool(raw["needs_verification"]))
        item["provenance"] = {"source": "google-gemini", "verified": False, "local_frame_qa": "required"}
        items.append(item)
    if items and len(items) == len(payload["items"]):
        status, error = "COMPLETE", None
    elif items:
        status, error = "PARTIAL", "some candidates rejected"
    else:
        status, error = "UNAVAILABLE", "no valid candidates"
    return SemanticVideoMapResult(status, items, error)


@dataclass
class SemanticVideoMapResult:
    status: str
    items: List[Dict[str, Any]]
    error: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {"status": self.status, "items": self.items, "error": self.error,
                "contract": "candidate-signal-only; local frame QA remains authoritative"}

    def write_artifacts(self, output_dir: Path, manifest: Optional[Dict[str, Any]] = None) -> Path:
        output_dir.mkdir(parents=True, exist_ok=True)
        path = output_dir / "semantic_video_map.json"
        path.write_text(json.dumps(self.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")
        if manifest is not None:
            manifest["semantic_video_map"] = {"status": self.status, "artifact": path.name,
                                               "candidate_count": len(self.items)}
            (output_dir / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
        return path


class SemanticVideoMapAdapter:
    def __init__(self, *, model: str = "gemini-2.5-flash", timeout: float = 120.0,
                 mock_payload: Optional[Dict[str, Any]] = None,
                 client_factory: Optional[Callable[[str], Any]] = None):
        self.model, self.timeout, self.mock_payload, self.client_factory = model, timeout, mock_payload, client_factory

    def analyze(self, video_path: Path, transcript_context: str = "") -> SemanticVideoMapResult:
        if self.mock_payload is not None:
            result = validate_semantic_map(self.mock_payload)
            return SemanticVideoMapResult("PARTIAL" if result.status == "COMPLETE" else result.status, result.items, result.error)
        key = os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")
        if not key:
            return SemanticVideoMapResult("DISABLED", [], "no Gemini API key configured")
        if not video_path.exists():
            return SemanticVideoMapResult("UNAVAILABLE", [], "video file does not exist")
        try:
            client = self.client_factory(key) if self.client_factory else self._default_client(key)
            uploaded = client.files.upload(file=str(video_path))
            deadline = time.monotonic() + self.timeout
            while getattr(uploaded, "state", None) and getattr(uploaded.state, "name", "") in {"PROCESSING", "PROCESSING_STATE"}:
                if time.monotonic() >= deadline:
                    return SemanticVideoMapResult("UNAVAILABLE", [], "Google Files API timeout")
                time.sleep(1)
                uploaded = client.files.get(name=uploaded.name)
            response = client.models.generate_content(
                model=self.model,
                contents=[uploaded, self._prompt(transcript_context)],
                config={"response_mime_type": "application/json"},
            )
            return validate_semantic_map_response(response)
        except Exception as exc:
            # Do not expose key, SDK payloads, or remote identifiers.
            return SemanticVideoMapResult("UNAVAILABLE", [], f"Google GenAI unavailable: {type(exc).__name__}")
        finally:
            # SDK cleanup is deliberately best-effort and never changes result status.
            try:
                if 'uploaded' in locals() and hasattr(client.files, "delete"):
                    client.files.delete(name=uploaded.name)
            except Exception:
                pass

    @staticmethod
    def _default_client(key: str) -> Any:
        from google import genai  # optional dependency
        return genai.Client(api_key=key)

    @staticmethod
    def _prompt(transcript_context: str = "") -> str:
        context = transcript_context[-12000:] if transcript_context else "(local transcript unavailable)"
        return ("Return JSON only: {items:[{start,end,type,description,spoken_evidence,visible_evidence,"
                "confidence,needs_verification}]}. Identify meeting topics, decisions and actions as candidates. "
                "Do not claim client-ready evidence; timestamps require local frame QA. Local Whisper transcript context:\n" + context)
