"""Meeting.v2 normalization, validation and client-facing renderers.

This track is deliberately conservative: model text is escaped, identities are never
inferred, and an image is emitted only when QA and semantic/OCR evidence agree.
"""
from __future__ import annotations

from html import escape
from pathlib import Path
import re
from typing import Any, Mapping, Optional

from .contract_validation import validate_meeting_document
from .evidence_selector import select_evidence_frame

STATUS = "DRAFT / DO POTWIERDZENIA"


def _text(value: Any, default: str = "DO POTWIERDZENIA") -> str:
    if value is None or str(value).strip() == "":
        return default
    return str(value).strip()


def _item_text(item: Any, keys: tuple[str, ...]) -> str:
    if isinstance(item, Mapping):
        for key in keys:
            if item.get(key):
                return _text(item[key])
    return _text(item)


def _key(value: str) -> str:
    value = re.sub(r"[^\w\s]", " ", value.lower(), flags=re.UNICODE)
    return " ".join(value.split())


def _dedupe(items: list[Any], text_keys: tuple[str, ...]) -> list[Any]:
    """Collapse overlap candidates while retaining evidence and marking conflicts."""
    result: list[Any] = []
    positions: dict[str, int] = {}
    for raw in items:
        item = dict(raw) if isinstance(raw, Mapping) else {"text": str(raw)}
        text = _item_text(item, text_keys)
        words = set(_key(text).split())
        match = None
        for old_key, pos in positions.items():
            old_words = set(old_key.split())
            if old_key == _key(text) or (words and old_words and len(words & old_words) / max(1, len(words | old_words)) >= .72):
                match = pos; break
        if match is None:
            positions[_key(text)] = len(result); result.append(item); continue
        prior = result[match]
        prior_e = prior.get("evidence")
        new_e = item.get("evidence")
        if prior_e and new_e and prior_e != new_e:
            prior["conflict"] = True
            prior.setdefault("conflict_note", "Kandydaci z nakładających się fragmentów mają różne dowody — DO POTWIERDZENIA.")
        if not prior_e and new_e:
            prior["evidence"] = new_e
    return result


def normalize_meeting(data: Optional[Mapping[str, Any]], title: str = "") -> dict[str, Any]:
    """Return a safe meeting.v2 payload suitable for both renderers.

    Item provenance is fail-closed here, not only in the CLI adapter: evidence is
    retained only when the item explicitly declares the corresponding frame ID.
    """
    src = dict(data or {})

    def normalize_evidence(evidence: Any, frame_ids: Any) -> list[dict[str, Any]]:
        allowed = {str(x) for x in frame_ids} if isinstance(frame_ids, list) else set()
        rows = [evidence] if isinstance(evidence, Mapping) else evidence
        return [dict(row) for row in rows if isinstance(rows, list) and isinstance(row, Mapping)
                and str(row.get("frame_id", row.get("frame_identifier", ""))) in allowed]

    def normalize_item(raw: Any, text_keys: tuple[str, ...]) -> dict[str, Any]:
        item = dict(raw) if isinstance(raw, Mapping) else {"text": raw}
        item["evidence"] = normalize_evidence(item.get("evidence", []), item.get("frame_ids"))
        return item

    def normalize_top_level_evidence(raw_items: Any) -> list[dict[str, Any]]:
        if not isinstance(raw_items, list):
            return []
        return [row for raw in raw_items if isinstance(raw, Mapping)
                for row in normalize_evidence(raw, raw.get("frame_ids"))]

    participants = []
    for i, raw in enumerate(src.get("participants", []) if isinstance(src.get("participants", []), list) else []):
        p = dict(raw) if isinstance(raw, Mapping) else {}
        participants.append({
            "speaker_label": _text(p.get("speaker_label"), f"SPEAKER_{i+1}"),
            "display_name": _text(p.get("display_name"), "DO UZUPEŁNIENIA"),
            "identity_status": p.get("identity_status") if p.get("identity_status") in ("explicit", "mentioned_only") else "mentioned_only",
        })
    actions = []
    for raw in src.get("action_items", []) if isinstance(src.get("action_items", []), list) else []:
        if isinstance(raw, Mapping) and str(raw.get("status", "")).lower() in {"rejected", "invalid"}:
            continue
        a = normalize_item(raw, ("task", "text", "action"))
        owner_explicit = bool(a.get("owner")) and a.get("owner") not in ("DO POTWIERDZENIA", "DO UZUPEŁNIENIA", "UNASSIGNED") and a.get("owner_status") == "assigned"
        due_explicit = bool(a.get("due")) and a.get("due") not in ("DO POTWIERDZENIA", "DO UZUPEŁNIENIA") and a.get("due_status") == "explicit"
        a["task"] = _item_text(a, ("task", "text", "action"))
        a["owner"] = _text(a.get("owner"), "UNASSIGNED") if owner_explicit else "UNASSIGNED"
        a["due"] = _text(a.get("due"), "DO POTWIERDZENIA") if due_explicit else "DO POTWIERDZENIA"
        actions.append(a)
    def clean_items(name: str, keys: tuple[str, ...]) -> list[Any]:
        raw_items = src.get(name, []) if isinstance(src.get(name, []), list) else []
        return _dedupe([normalize_item(x, keys) for x in raw_items
                        if not (isinstance(x, Mapping) and str(x.get("status", "")).lower() in {"rejected", "invalid"})], keys)

    topics = src.get("topics", []) if isinstance(src.get("topics", []), list) else []
    topics = [normalize_item(x, ("text", "topic", "title")) for x in topics
              if not (isinstance(x, Mapping) and str(x.get("status", "")).lower() in {"rejected", "invalid"})]
    if not topics and isinstance(src.get("steps"), list):
        topics = [{"text": x.get("title") or x.get("description")} for x in src["steps"] if isinstance(x, Mapping) and (x.get("title") or x.get("description"))]
    return {
        "title": _text(src.get("title"), title or "Spotkanie"),
        "meeting_date": _text(src.get("meeting_date") or src.get("date")),
        "purpose": _text(src.get("purpose") or src.get("intro")),
        "participants": participants,
        "topics": topics,
        "decisions": _dedupe([normalize_item(x, ("decision", "text")) for x in (src.get("decisions", []) if isinstance(src.get("decisions", []), list) else []) if not (isinstance(x, Mapping) and str(x.get("status", "")).lower() in {"rejected", "invalid"})], ("decision", "text")),
        "agreements": _dedupe([normalize_item(x, ("text", "agreement")) for x in (src.get("agreements", []) if isinstance(src.get("agreements", []), list) else []) if not (isinstance(x, Mapping) and str(x.get("status", "")).lower() in {"rejected", "invalid"})], ("text", "agreement")),
        "proposed": _dedupe([normalize_item(x, ("text", "proposal")) for x in (src.get("proposed", []) if isinstance(src.get("proposed", []), list) else []) if not (isinstance(x, Mapping) and str(x.get("status", "")).lower() in {"rejected", "invalid"})], ("text", "proposal")),
        "action_items": actions,
        "open_questions": clean_items("open_questions", ("text", "question")),
        "risks": clean_items("risks", ("text", "risk")),
        "evidence": normalize_top_level_evidence(src.get("evidence", [])),
        "status": "DRAFT",
    }


def validate_meeting_v2(data: Optional[Mapping[str, Any]], exists=None) -> list[str]:
    doc = normalize_meeting(data)
    return validate_meeting_document(doc, exists=exists) if doc.get("participants") or doc.get("evidence") else []


def _evidence(item: Any, base_dir: Optional[Path]) -> Optional[str]:
    if not isinstance(item, Mapping): return None
    candidates = item.get("evidence", [])
    if isinstance(candidates, Mapping): candidates = [candidates]
    prepared = []
    for candidate in candidates if isinstance(candidates, list) else []:
        if not isinstance(candidate, Mapping):
            continue
        row = dict(candidate)
        # Meeting fixture aliases are normalized to the shared fail-closed selector.
        row.setdefault("qa_verdict", row.get("frame_qa"))
        row.setdefault("semantic_relation", row.get("semantic_match"))
        prepared.append(row)
    accepted = select_evidence_frame(prepared)
    if not accepted: return None
    path = Path(str(accepted.get("frame_path", accepted.get("frame", ""))))
    if base_dir:
        base_resolved = Path(base_dir).resolve()
        path = (base_resolved / path).resolve() if not path.is_absolute() else path.resolve()
        if not path.is_relative_to(base_resolved):
            return None
    if not path.exists(): return None
    if base_dir:
        try:
            return path.relative_to(Path(base_dir).resolve()).as_posix()
        except ValueError:
            return None
    return path.as_posix()


def _md_list(items, keys):
    if not items: return ["- DO POTWIERDZENIA"]
    out = []
    for raw in items:
        text = _item_text(raw, keys)
        suffix = " — KONFLIKT: DO POTWIERDZENIA" if isinstance(raw, Mapping) and raw.get("conflict") else ""
        out.append(f"- {text}{suffix}")
    return out


def render_meeting_markdown_v2(title: str, data: Optional[Mapping[str, Any]], base_dir: Optional[Path] = None) -> str:
    d = normalize_meeting(data, title)
    lines = [f"# Protokół spotkania: {d['title']}", "", f"**Status:** {STATUS}", "", "## Data i cel spotkania", f"- **Data:** {d['meeting_date']}", f"- **Cel:** {d['purpose']}", "", "## Uczestnicy"]
    lines += [f"- **{p['speaker_label']}** — {p['display_name']} ({p['identity_status']})" for p in d["participants"]] or ["- DO UZUPEŁNIENIA"]
    lines += ["", "## Tematy"] + _md_list(d["topics"], ("text", "topic", "title"))
    lines += ["", "## Decyzje", "### Uzgodnione decyzje"] + ([f"- {_item_text(x, ('decision', 'text'))} [timestamp: {_text((x.get('evidence') or {}).get('timestamp')) if isinstance(x, Mapping) and isinstance(x.get('evidence'), Mapping) else 'DO POTWIERDZENIA'}]" for x in d["decisions"]] if d["decisions"] else ["- DO POTWIERDZENIA — nie wykryto zweryfikowanych decyzji"])
    lines += ["", "## Uzgodnienia / propozycje / omówione"] + _md_list(d["agreements"] + d["proposed"], ("text", "agreement", "proposal"))
    lines += ["", "## Action items", "| # | Zadanie | Owner | Termin |", "|---:|---|---|---|"]
    lines += [f"| {i} | {a['task']} | {a['owner']} | {a['due']} |" for i, a in enumerate(d["action_items"], 1)] or ["| 1 | DO POTWIERDZENIA | UNASSIGNED | DO POTWIERDZENIA |"]
    lines += ["", "## Pytania otwarte"] + _md_list(d["open_questions"], ("text", "question"))
    lines += ["", "## Ryzyka"] + _md_list(d["risks"], ("text", "risk"))
    lines += ["", "## Dowody"]
    rendered = False
    for raw in d["decisions"] + d["agreements"] + d["action_items"]:
        path = _evidence(raw, base_dir)
        if path:
            lines.append(f"- [{Path(path).name}]({path})"); rendered = True
    if not rendered: lines.append("- DO POTWIERDZENIA — brak pliku lub zaakceptowanego dowodu obrazu")
    lines += ["", "## Prośba o potwierdzenie", "Prosimy o potwierdzenie daty, uczestników, decyzji, właścicieli i terminów. Do tego czasu dokument pozostaje DRAFT / DO POTWIERDZENIA."]
    return "\n".join(lines) + "\n"


def render_meeting_html_v2(title: str, data: Optional[Mapping[str, Any]], output_html: Path, base_dir: Optional[Path] = None) -> Path:
    d = normalize_meeting(data, title)
    def h(x): return escape(str(x), quote=True)
    sections = [("Tematy", d["topics"], ("text", "topic", "title")), ("Uzgodnione decyzje", d["decisions"], ("decision", "text")), ("Uzgodnienia / propozycje / omówione", d["agreements"] + d["proposed"], ("text", "agreement", "proposal")), ("Pytania otwarte", d["open_questions"], ("text", "question")), ("Ryzyka", d["risks"], ("text", "risk"))]
    body = [f"<h1>{h(d['title'])}</h1><p><b>Status:</b> {STATUS}</p>", f"<h2>Data i cel spotkania</h2><p><b>Data:</b> {h(d['meeting_date'])}<br><b>Cel:</b> {h(d['purpose'])}</p>", "<h2>Uczestnicy</h2><ul>"]
    body += [f"<li><b>{h(p['speaker_label'])}</b> — {h(p['display_name'])} ({h(p['identity_status'])})</li>" for p in d["participants"]] or ["<li>DO UZUPEŁNIENIA</li>"]
    body.append("</ul>")
    for heading, items, keys in sections:
        body += [f"<h2>{h(heading)}</h2><ul>"] + [f"<li>{h(_item_text(x, keys))}{' — KONFLIKT: DO POTWIERDZENIA' if isinstance(x, Mapping) and x.get('conflict') else ''}</li>" for x in items] + (["<li>DO POTWIERDZENIA</li>"] if not items else []) + ["</ul>"]
    body += ["<h2>Action items</h2><table><tr><th>#</th><th>Zadanie</th><th>Owner</th><th>Termin</th></tr>"]
    body += [f"<tr><td>{i}</td><td>{h(a['task'])}</td><td>{h(a['owner'])}</td><td>{h(a['due'])}</td></tr>" for i, a in enumerate(d["action_items"], 1)] or ["<tr><td>—</td><td>DO POTWIERDZENIA</td><td>UNASSIGNED</td><td>DO POTWIERDZENIA</td></tr>"]
    body.append("</table><h2>Dowody</h2>")
    images = []
    for raw in d["decisions"] + d["agreements"] + d["action_items"]:
        path = _evidence(raw, base_dir)
        if path: images.append(f'<img src="{h(Path(path).as_posix())}" alt="zaakceptowany dowód">')
    body += images or ["<p>Brak zaakceptowanych dowodów obrazu — DO POTWIERDZENIA</p>"]
    body += ["<h2>Prośba o potwierdzenie</h2><p>Prosimy o potwierdzenie treści. Dokument pozostaje DRAFT / DO POTWIERDZENIA.</p>"]
    output_html.parent.mkdir(parents=True, exist_ok=True); output_html.write_text("<!doctype html><meta charset='utf-8'><style>body{font:16px sans-serif;max-width:900px;margin:2em auto}table{border-collapse:collapse;width:100%}td,th{border:1px solid #ccc;padding:6px}img{max-width:100%;display:block;margin:1em 0}</style>" + "".join(body), encoding="utf-8")
    return output_html
