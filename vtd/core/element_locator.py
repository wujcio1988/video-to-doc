"""
Lokalizacja elementów interfejsu (OCR TSV) oraz planowanie adnotacji wizualnych przez LLM.
"""
from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any, Dict, List, Optional

from vtd.config import load_config
from vtd.providers import get_llm_provider, robust_json_decode


def locate_words(frame_path: Path, words: List[str]) -> Dict[str, List[int]]:
    """
    Dla każdej szukanej frazy zwraca współrzędne ramki [x1, y1, x2, y2].
    Wykorzystuje wyjście TSV z Tesseract OCR (conf >= 60).
    """
    if not words:
        return {}
    p = Path(frame_path)
    if not p.exists():
        return {}

    try:
        res = subprocess.run(
            ["tesseract", str(p), "stdout", "-l", "pol+eng", "--psm", "6", "tsv"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        if res.returncode != 0 or not res.stdout or not res.stdout.strip():
            return {}

        lines = res.stdout.splitlines()
        if not lines:
            return {}

        header = lines[0].strip().split("\t")
        try:
            left_idx = header.index("left")
            top_idx = header.index("top")
            width_idx = header.index("width")
            height_idx = header.index("height")
            conf_idx = header.index("conf")
            text_idx = header.index("text")
        except ValueError:
            return {}

        entries = []
        for line in lines[1:]:
            parts = line.split("\t")
            if len(parts) <= max(left_idx, top_idx, width_idx, height_idx, conf_idx, text_idx):
                continue
            text = parts[text_idx].strip()
            if not text:
                continue
            try:
                conf = int(float(parts[conf_idx]))
            except Exception:
                conf = -1
            if conf < 60:
                continue
            try:
                left = int(parts[left_idx])
                top = int(parts[top_idx])
                w = int(parts[width_idx])
                h = int(parts[height_idx])
            except Exception:
                continue
            if w <= 0 or h <= 0:
                continue
            x1, y1, x2, y2 = left, top, left + w, top + h
            entries.append({"text": text, "box": [x1, y1, x2, y2], "conf": conf})

        result: Dict[str, List[int]] = {}
        best_map: Dict[str, Dict[str, Any]] = {}
        for sw in words:
            if not sw or not sw.strip():
                continue
            sw_norm = sw.lower().strip()
            best = None
            best_conf = -1
            for e in entries:
                e_text_lower = e["text"].lower()
                if sw_norm in e_text_lower or e_text_lower in sw_norm:
                    if e["conf"] > best_conf:
                        best_conf = e["conf"]
                        best = e
            if best is not None:
                if sw not in best_map or best["conf"] > best_map[sw]["conf"]:
                    best_map[sw] = best

        for k, v in best_map.items():
            result[k] = v["box"]
        return result
    except (subprocess.TimeoutExpired, FileNotFoundError, Exception):
        return {}


def plan_annotations(
    step_number: int,
    step_title: str,
    step_description: str,
    located: Dict[str, List[int]],
    model: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """
    Decyduje, które elementy interfejsu oznaczyć adnotacją (maksymalnie 3 elementy).
    Posiada automatyczny fallback w 100% offline, gdy model LLM jest niedostępny.
    """
    if not located:
        return []

    def _fallback() -> List[Dict[str, Any]]:
        desc_lower = (step_description or "").lower()
        for word, box in located.items():
            if word.lower() in desc_lower:
                return [{"type": "arrow", "box": box, "element": word, "label": word}]
        return []

    cfg = load_config()
    chosen_model = model or cfg.llm.element_locator_model
    provider = get_llm_provider(cfg.llm)

    try:
        located_items_str = ", ".join([f"{k}: {v}" for k, v in located.items()])
        system_msg = "Jesteś ekspertem adnotacji UI. Odpowiadasz TYLKO JSON."
        user_msg = (
            f"Krok {step_number}: '{step_title}' — {step_description}\n"
            f"Znalezione elementy (słowo + box [x1,y1,x2,y2]): {located_items_str}\n"
            f"Wybierz MAKSYMALNIE 3 elementy kluczowe dla tego kroku. Dla każdego podaj typ: "
            f"arrow (jeden konkretny przycisk/pole) | highlight (obszar/zakładkę) | badge z numerem wg kolejności użycia w opisie | dim (jeśli element jest trudny do znalezienia na zatłoczonym ekranie).\n"
            f"Odpowiedz TYLKO JSON: {{\"annotations\":[{{\"element\":\"<słowo z listy>\",\"type\":\"...\",\"number\":int?}}]}}."
        )

        content = provider.chat_completion(
            messages=[
                {"role": "system", "content": system_msg},
                {"role": "user", "content": user_msg},
            ],
            model=chosen_model,
            temperature=0.2,
            max_tokens=800,
            json_mode=True,
        )

        parsed = robust_json_decode(content)
        anns_raw = parsed.get("annotations", [])
        if not isinstance(anns_raw, list):
            anns_raw = []

        result: List[Dict[str, Any]] = []
        for idx, entry in enumerate(anns_raw):
            if not isinstance(entry, dict):
                continue
            element = entry.get("element") or entry.get("word") or entry.get("label")
            if not element:
                continue

            matched_box = None
            for loc_word, box in located.items():
                if str(element).lower() in loc_word.lower() or loc_word.lower() in str(element).lower():
                    matched_box = box
                    break

            if not matched_box:
                continue

            ann_type = str(entry.get("type", "arrow")).strip().lower()
            if ann_type not in ("arrow", "highlight", "badge", "dim"):
                ann_type = "arrow"

            item: Dict[str, Any] = {
                "type": ann_type,
                "box": matched_box,
                "element": str(element),
                "label": str(element),
            }
            if ann_type == "badge":
                num = entry.get("number")
                if num is not None:
                    try:
                        item["number"] = int(num)
                    except Exception:
                        item["number"] = idx + 1
                else:
                    item["number"] = idx + 1

            result.append(item)
            if len(result) >= 3:
                break

        return result if result else _fallback()
    except Exception:
        return _fallback()
