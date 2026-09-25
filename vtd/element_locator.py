"""Lokalizacja elementow UI na klatce (OCR TSV) + planowanie adnotacji przez LLM."""
import subprocess
import json
import urllib.request
from pathlib import Path
from typing import List, Dict, Any, Optional

def locate_words(frame_path: Path, words: List[str], ocr_func=None) -> Dict[str, List[int]]:
    """
    Dla kazdej szukanej frazy zwraca najlepszy box [x1,y1,x2,y2].
    - Uzywa tesseract TSV (pol+eng --psm 6) z timeout 10s.
    - Dopasowanie po podciagu case-insensitive, conf >=60.
    - Gdy tesseract padnie -> pusty dict.
    - ocr_func jesli podane, uzyj do mockowania w testach? ignorowane, zachowana kompatybilnosc.
    """
    if not words:
        return {}
    p = Path(frame_path)
    if not p.exists():
        return {}
    # Try tesseract TSV
    try:
        # Call tesseract
        res = subprocess.run(
            ["tesseract", str(p), "stdout", "-l", "pol+eng", "--psm", "6", "tsv"],
            capture_output=True,
            text=True,
            timeout=10
        )
        if res.returncode != 0:
            # fallback to plain? but spec says empty dict on fail
            return {}
        stdout = res.stdout
        if not stdout or not stdout.strip():
            return {}
        lines = stdout.splitlines()
        if not lines:
            return {}
        header = lines[0].strip().split("\t")
        # Expected header: level, page_num, block_num, par_num, line_num, word_num, left, top, width, height, conf, text
        # Find indices
        try:
            left_idx = header.index("left")
            top_idx = header.index("top")
            width_idx = header.index("width")
            height_idx = header.index("height")
            conf_idx = header.index("conf")
            text_idx = header.index("text")
        except ValueError:
            return {}
        # Collect word entries
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
        # For each searched word, find best box where entry text contains substring or vice versa? spec: dopasowanie po podciagu dla odmiany — "Surowc" lapie "Surowce"/"Surowiec"
        # So searched word substring in entry text (case-insensitive) OR entry text substring in searched word? spec example: search "Surowc" should match "Surowce" -> search substring in found text.
        # Also should be case-insensitive.
        best_map: Dict[str, Dict[str, Any]] = {}
        for sw in words:
            if not sw or not sw.strip():
                continue
            sw_lower = sw.lower().strip()
            sw_norm = sw_lower
            best = None
            best_conf = -1
            for e in entries:
                e_text_lower = e["text"].lower()
                # substring match either direction: if searched is substring of found OR found substring of searched? spec says searched substring catches found variations. But for robustness allow both.
                if sw_norm in e_text_lower or e_text_lower in sw_norm:
                    if e["conf"] > best_conf:
                        best_conf = e["conf"]
                        best = e
                # also try without diacritics? skip
            if best is not None:
                # Keep best per word key (original word)
                # If multiple entries match same word, pick highest conf
                if sw not in best_map or best["conf"] > best_map[sw]["conf"]:
                    best_map[sw] = best
        for k, v in best_map.items():
            result[k] = v["box"]
        return result
    except subprocess.TimeoutExpired:
        return {}
    except FileNotFoundError:
        return {}
    except Exception:
        return {}

def _get_token():
    try:
        from vtd.enricher import get_omniroute_token as _get
        return _get()
    except Exception:
        return ""

def plan_annotations(step_number: int, step_title: str, step_description: str, located: Dict[str, List[int]], llm_model: str = "erp-code") -> List[Dict[str, Any]]:
    """
    LLM decyduje ktore elementy adnotowac. Fallback deterministyczny gdy LLM niedostepny.
    Zwraca liste max 3 dict {type, box, number?, element?, label?}
    """
    if not located:
        return []
    # Build prompt
    # Need to decide max 3
    # Fallback helper
    def _fallback() -> List[Dict[str, Any]]:
        # 1 arrow na pierwszy element z located, ktorego slowo wystepuje w step_description substring case-insensitive
        desc_lower = (step_description or "").lower()
        for word, box in located.items():
            if word.lower() in desc_lower:
                return [{"type": "arrow", "box": box, "element": word, "label": word}]
        # if none match, if located non-empty, fallback to first entry? spec says "pierwszy element z located, ktorego slowo wystepuje w step_description". If none matches, return empty? But spec says "Zawsze cos sensownego, jesli tylko OCR cos znalazl." So fallback to first?
        # Interpret: if OCR found something but none in description, still return empty? We'll be conservative: return [] if no substring match, but spec says always something sensible if OCR found something. Let's interpret as: if OCR found something and located non-empty, fallback returns 1 arrow on first element regardless if no word in description? But spec says "ktorego slowo wystepuje w step_description". So if none matches, fallback gives empty. We'll follow spec strictly.
        return []

    # Try LLM
    try:
        token = _get_token()
        url = "http://127.0.0.1:20128/v1/chat/completions"
        # Build located list description
        located_items_str = ", ".join([f"{k}: {v}" for k, v in located.items()])
        # If located empty we already returned
        system_msg = "Jestes ekspertem adnotacji UI. Odpowiadasz TYLKO JSON."
        user_msg = (
            f"Krok {step_number}: '{step_title}' — {step_description}\n"
            f"Znalezione elementy (slowo + box [x1,y1,x2,y2]): {located_items_str}\n"
            f"Wybierz MAKSYMALNIE 3 elementy kluczowe dla tego kroku. Dla kazdego podaj typ: "
            f"arrow (jeden konkretny przycisk/pole) | highlight (obszar/zakladke) | badge z numerem wg kolejnosci uzycia w opisie | dim (jesli element jest trudny do znalezienia na zatloczonym ekranie). "
            f"Odpowiedz TYLKO JSON: {{\"annotations\":[{{\"element\":\"<slowo z listy>\",\"type\":\"...\",\"number\":int?}}]}}."
        )
        payload = {
            "model": llm_model,
            "messages": [
                {"role": "system", "content": system_msg},
                {"role": "user", "content": user_msg},
            ],
            "temperature": 0.2,
            "max_tokens": 800,
        }
        req = urllib.request.Request(
            url,
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {token}",
            },
        )
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            content = data["choices"][0]["message"]["content"].strip()
            # robust decode
            try:
                from vtd.enricher import robust_json_decode as _rjd
                parsed = _rjd(content)
            except Exception:
                parsed = json.loads(content)
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
                typ = str(entry.get("type", "arrow")).lower().strip()
                if typ not in ("arrow", "highlight", "badge", "dim"):
                    typ = "arrow"
                # Map element -> box via exact or substring (case-insensitive)
                box = None
                el_lower = str(element).lower()
                # exact match first
                if element in located:
                    box = located[element]
                else:
                    # substring search
                    for k, b in located.items():
                        if el_lower in k.lower() or k.lower() in el_lower:
                            box = b
                            element = k  # normalize to located key
                            break
                if box is None:
                    continue
                # Build annotation dict
                ann: Dict[str, Any] = {"type": typ, "box": box, "element": element, "label": element}
                if typ == "badge":
                    num = entry.get("number")
                    try:
                        num = int(num) if num is not None else idx + 1
                    except Exception:
                        num = idx + 1
                    ann["number"] = num
                # preserve number if provided for other types?
                if "number" in entry and typ != "badge":
                    try:
                        ann["number"] = int(entry["number"])
                    except Exception:
                        pass
                result.append(ann)
                if len(result) >= 3:
                    break
            # If LLM returned more than 3, we already truncated to first 3 in order
            if result:
                return result[:3]
            # if result empty but LLM gave something but none mapped, fallback
            # If LLM returned empty list, return []
            if len(anns_raw) == 0:
                return []
            # If mapping failed entirely, fallback
            return _fallback()
    except Exception as e:
        # Network/JSON error -> fallback
        return _fallback()
