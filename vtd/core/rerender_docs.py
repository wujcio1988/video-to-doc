#!/usr/bin/env python3
"""
Rerender clean client-ready docs for an existing output dir.
Usage: python -m video_to_manual.rerender_docs <output_dir>
Or: python tools/video_to_manual/rerender_docs.py <output_dir>

Reads metadata.json + attempts to reconstruct ManualSteps from:
  1. PROMPT_DLA_AGENTA.txt (KROK markers)
  2. Existing INSTRUKCJA.md Dowód lines + headings
  3. frames/ scan fallback

Then regenerates INSTRUKCJA.md/.html with clean builders (no Dowód, no raw dict)
and replaces files in output_dir and client-ready/ with backup *.bak.<ts>.
"""
import argparse
import json
import re
import shutil
from datetime import datetime
from pathlib import Path
from typing import List, Optional, Dict, Any

from vtd.core.fusion import ManualStep
from vtd.builders.manual_builder import render_markdown_manual
from vtd.builders.html_builder import render_html_manual


def _timestamp() -> str:
    return datetime.now().strftime("%Y%m%d_%H%M%S")


def _backup_file(p: Path) -> Optional[Path]:
    if not p.exists():
        return None
    ts = _timestamp()
    bak = p.with_suffix(p.suffix + f".bak.{ts}")
    # ensure unique if second within same second
    counter = 0
    while bak.exists():
        counter += 1
        bak = p.with_suffix(p.suffix + f".bak.{ts}.{counter}")
    shutil.copy2(p, bak)
    return bak


def _load_metadata(output_dir: Path) -> Dict[str, Any]:
    mp = output_dir / "metadata.json"
    if not mp.exists():
        return {}
    try:
        return json.loads(mp.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _parse_steps_from_prompt(output_dir: Path) -> List[ManualStep]:
    prompt_path = output_dir / "PROMPT_DLA_AGENTA.txt"
    if not prompt_path.exists():
        return []
    text = prompt_path.read_text(encoding="utf-8", errors="ignore")
    # Pattern: --- KROK 1 (Znacznik: 0.0s - 42.2s | klatka: scene_0001.png | źródło: ...
    # Also need speech text line: Wypowiedź lektora: ...
    steps: List[ManualStep] = []
    # Find blocks
    pattern = re.compile(
        r"---\s*KROK\s+(\d+).*?Znacznik:\s*([0-9.]+)s\s*-\s*([0-9.]+)s.*?klatka:\s*([^\s|]+)",
        re.IGNORECASE | re.DOTALL,
    )
    for m in pattern.finditer(text):
        try:
            num = int(m.group(1))
            start = float(m.group(2))
            end = float(m.group(3))
            frame_name = m.group(4).strip()
            frame_path = output_dir / "frames" / frame_name
            # try to get speech_text: after this marker, look for "Wypowiedź lektora: (.*?)\\nDowód"
            block_start = m.end()
            block_end = text.find("--- KROK", block_start)
            if block_end == -1:
                block_end = len(text)
            block = text[block_start:block_end]
            speech_m = re.search(r"Wypowiedź lektora:\s*(.*?)\nDowód:", block, re.DOTALL)
            speech = speech_m.group(1).strip() if speech_m else ""
            steps.append(ManualStep(step_number=num, start_time=start, end_time=end, frame_path=frame_path, speech_text=speech))
        except Exception:
            continue
    steps.sort(key=lambda s: s.step_number)
    return steps


def _parse_steps_from_existing_markdown(output_dir: Path) -> tuple[List[ManualStep], Dict[int, Dict[str, str]]]:
    md_path = output_dir / "INSTRUKCJA.md"
    if not md_path.exists():
        return [], {}
    text = md_path.read_text(encoding="utf-8", errors="ignore")
    steps: List[ManualStep] = []
    enriched: Dict[int, Dict[str, str]] = {}
    # Find all Krok headings and following Dowód
    # Use regex to iterate
    heading_pat = re.compile(r"###\s*Krok\s+(\d+):\s*(.*?)\n")
    dowód_pat = re.compile(r"\*\*Dowód:\*\*.*?klatka\s*`([^`]+)`\s*\|\s*timestamp\s*`([0-9.]+)s\s*-\s*([0-9.]+)s`", re.IGNORECASE)
    # Also capture image path
    # We'll split by heading
    for hm in heading_pat.finditer(text):
        num = int(hm.group(1))
        title = hm.group(2).strip()
        # search dowód after this heading
        start_idx = hm.end()
        next_heading = heading_pat.search(text, start_idx)
        end_idx = next_heading.start() if next_heading else len(text)
        block = text[start_idx:end_idx]
        dm = dowód_pat.search(block)
        if dm:
            frame_name = dm.group(1).strip()
            start_t = float(dm.group(2))
            end_t = float(dm.group(3))
            frame_path = output_dir / "frames" / frame_name
            # description/opis operacji maybe
            desc_m = re.search(r"\*\*Opis operacji:\*\*\s*(.*?)\n", block, re.DOTALL)
            desc = desc_m.group(1).strip() if desc_m else ""
            steps.append(ManualStep(step_number=num, start_time=start_t, end_time=end_t, frame_path=frame_path, speech_text=desc))
            if title:
                enriched[num] = {"title": title, "description": desc}
        else:
            # no dowód, try to get frame from image
            img_m = re.search(r"!\[Krok\s*\d+\]\(([^)]+)\)", block)
            frame_name = Path(img_m.group(1)).name if img_m else f"scene_{num:04d}.png"
            frame_path = output_dir / "frames" / frame_name
            steps.append(ManualStep(step_number=num, start_time=0.0, end_time=5.0, frame_path=frame_path, speech_text=""))
            if title:
                enriched[num] = {"title": title}
    # Also handle case where heading has no colon title (clean new format) -> heading_pat won't match, try alternative
    if not steps:
        alt_heading_pat = re.compile(r"###\s*Krok\s+(\d+)[^\n]*\n")
        for hm in alt_heading_pat.finditer(text):
            num = int(hm.group(1))
            # try to extract title after colon
            full = hm.group(0)
            title = ""
            if ":" in full:
                title = full.split(":", 1)[1].strip()
            block_start = hm.end()
            next_h = alt_heading_pat.search(text, block_start)
            block_end = next_h.start() if next_h else len(text)
            block = text[block_start:block_end]
            # try image
            img_m = re.search(r"!\[Krok\s*\d+\]\(([^)]+)\)", block)
            frame_name = Path(img_m.group(1)).name if img_m else f"scene_{num:04d}.png"
            frame_path = output_dir / "frames" / frame_name
            steps.append(ManualStep(step_number=num, start_time=0.0, end_time=5.0, frame_path=frame_path, speech_text=""))
            if title:
                enriched[num] = {"title": title}
    steps.sort(key=lambda s: s.step_number)
    return steps, enriched


def _parse_steps_from_frames(output_dir: Path) -> List[ManualStep]:
    frames_dir = output_dir / "frames"
    if not frames_dir.exists():
        return []
    frames = sorted(frames_dir.glob("*.png"))
    if not frames:
        frames = sorted(frames_dir.glob("*.jpg"))
    steps: List[ManualStep] = []
    for i, fp in enumerate(frames, start=1):
        # dummy timestamps sequential 0, 5, 10...
        start = float((i - 1) * 5)
        end = float(i * 5)
        steps.append(ManualStep(step_number=i, start_time=start, end_time=end, frame_path=fp, speech_text=""))
    return steps


def _load_enriched_from_old(output_dir: Path, parsed_enriched: Dict[int, Dict[str, str]]) -> Optional[Dict[str, Any]]:
    # Try to build enriched_data from parsed titles/descriptions plus old markdown callouts
    if not parsed_enriched:
        return None
    steps_list = []
    for num, info in parsed_enriched.items():
        steps_list.append({"step_number": num, "title": info.get("title", ""), "description": info.get("description", ""), "source": "narration/transcript"})
    # intro: try to extract Cel procedury from markdown
    intro = ""
    md_path = output_dir / "INSTRUKCJA.md"
    if md_path.exists():
        text = md_path.read_text(encoding="utf-8", errors="ignore")
        # look for ## Cel procedury following block
        m = re.search(r"##\s*Cel procedury\s*\n+(.*?)\n+##", text, re.DOTALL)
        if m:
            intro = m.group(1).strip()
            # remove markdown markers
            intro = intro.split("\n")[0].strip()
    return {"intro": intro, "steps": steps_list} if steps_list else None


def rerender_output_dir(output_dir: Path) -> Dict[str, Any]:
    output_dir = Path(output_dir).resolve()
    if not output_dir.exists():
        raise FileNotFoundError(f"Output dir not found: {output_dir}")
    metadata = _load_metadata(output_dir)
    title = metadata.get("title") or output_dir.name
    mode = metadata.get("mode") or "manual"
    # Try reconstruction in order: prompt > existing md > frames
    steps: List[ManualStep] = []
    enriched_data: Optional[Dict[str, Any]] = None
    parsed_enriched: Dict[int, Dict[str, str]] = {}

    # 1. prompt
    steps = _parse_steps_from_prompt(output_dir)
    if steps:
        # also try to get titles from existing md for enriched
        _, parsed_enriched = _parse_steps_from_existing_markdown(output_dir)
        if parsed_enriched:
            enriched_data = _load_enriched_from_old(output_dir, parsed_enriched)
    # 2. existing md if prompt gave nothing
    if not steps:
        steps, parsed_enriched = _parse_steps_from_existing_markdown(output_dir)
        if parsed_enriched:
            enriched_data = _load_enriched_from_old(output_dir, parsed_enriched)
    # 3. frames fallback
    if not steps:
        steps = _parse_steps_from_frames(output_dir)
        if not steps:
            raise RuntimeError(f"No steps reconstructable in {output_dir} (no prompt, no markdown, no frames)")

    # If still no enriched, try to keep previous enriched titles by re-parsing prompt speech?
    # Ensure steps sorted and have correct frame_path absolute
    steps.sort(key=lambda s: s.step_number)

    # Prepare enriched_data for rendering: need titles plus descriptions
    # If we have parsed_enriched but steps came from prompt, merge titles into enriched_data
    if enriched_data and steps:
        # enriched_data already contains titles; ensure step descriptions prefer enriched
        pass
    elif parsed_enriched and not enriched_data:
        enriched_data = _load_enriched_from_old(output_dir, parsed_enriched)

    # Fallback: try to read existing metadata's steps if any? (future)
    # If metadata already has stats, preserve

    # Render clean docs
    md_text = render_markdown_manual(title=title, steps=steps, base_dir=output_dir, enriched_data=enriched_data, metadata=metadata, mode=mode)
    # For html, need to render to tmp first then write
    html_path_tmp = output_dir / "_tmp_rerender.html"
    render_html_manual(title=title, steps=steps, output_html=html_path_tmp, enriched_data=enriched_data, metadata=metadata, mode=mode)
    html_text = html_path_tmp.read_text(encoding="utf-8")
    html_path_tmp.unlink(missing_ok=True)

    # Backup and overwrite
    backups = {}
    for fname, content, is_html in [
        ("INSTRUKCJA.md", md_text, False),
        ("INSTRUKCJA.html", html_text, True),
    ]:
        target = output_dir / fname
        bak = _backup_file(target)
        if bak:
            backups[str(target)] = str(bak)
        target.write_text(content, encoding="utf-8")
        # also client-ready if exists
        cr = output_dir / "client-ready" / fname
        if cr.exists() or (output_dir / "client-ready").exists():
            bak2 = _backup_file(cr) if cr.exists() else None
            if bak2:
                backups[str(cr)] = str(bak2)
            cr.parent.mkdir(parents=True, exist_ok=True)
            cr.write_text(content, encoding="utf-8")

    return {"output_dir": str(output_dir), "steps": len(steps), "backups": backups, "title": title, "mode": mode}


def main():
    parser = argparse.ArgumentParser(description="Rerender clean client-ready docs (remove Dowód, raw dict) with backups")
    parser.add_argument("output_dir", type=str, help="Katalog outputu (zawiera metadata.json)")
    args = parser.parse_args()
    result = rerender_output_dir(Path(args.output_dir))
    print(f"[RERENDER] {result['output_dir']} steps={result['steps']} backups={len(result['backups'])}")
    for k, v in result["backups"].items():
        print(f"  backup {k} -> {v}")


if __name__ == "__main__":
    main()
