"""
Moduł wzbogacania i redagowania kroków instrukcji za pomocą modeli LLM.
Obsługuje dowolnego dostawcę zgodnego z OpenAI API.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
from pathlib import Path
from typing import Any, Dict, List, Optional

from vtd.config import load_config
from vtd.core.fusion import ManualStep
from vtd.providers import get_llm_provider, robust_json_decode


def extract_frame_ocr(frame_path: Path) -> str:
    """Wyciąga tekst z klatki za pomocą Tesseract OCR."""
    if not frame_path.exists():
        return ""
    try:
        res = subprocess.run(
            ["tesseract", str(frame_path), "stdout", "-l", "pol+eng", "--psm", "6"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        lines = [line.strip() for line in res.stdout.splitlines() if len(line.strip()) > 2]
        return "\n".join(lines[:20])
    except Exception:
        return ""


def find_relevant_reference_docs(query_text: str, docs_dir: Optional[Path] = None, max_chars: int = 3000) -> str:
    """Wyszukuje fragmenty powiązanej dokumentacji referencyjnej z plików Markdown."""
    if not docs_dir or not docs_dir.exists():
        return ""

    matches = []
    keywords = ["procedura", "instrukcja", "krok", "wymagania", "konfiguracja", "proces"]
    query_lower = query_text.lower()

    for md_file in docs_dir.glob("**/*.md"):
        try:
            content = md_file.read_text(encoding="utf-8", errors="ignore")
            for kw in keywords:
                if kw in query_lower and kw in content.lower():
                    paragraphs = content.split("\n\n")
                    for p in paragraphs:
                        if kw in p.lower() and len(p.strip()) > 60:
                            matches.append(p.strip())
                            if sum(len(m) for m in matches) >= max_chars:
                                break
                if sum(len(m) for m in matches) >= max_chars:
                    break
        except Exception:
            continue
        if sum(len(m) for m in matches) >= max_chars:
            break

    if not matches:
        return ""

    joined = "\n\n---\n\n".join(matches[:4])
    return joined[:max_chars]


def enrich_steps_with_llm(
    title: str,
    steps: List[ManualStep],
    model: Optional[str] = None,
    timeout: int = 180,
    client: Optional[str] = None,
    process: Optional[str] = None,
    module: Optional[str] = None,
    environment: Optional[str] = None,
    author: Optional[str] = None,
    document_status: str = "DRAFT",
    mode: str = "manual",
    reference_dir: Optional[Path] = None,
) -> Optional[Dict[str, Any]]:
    """
    Redaguje zwięzłą instrukcję (manual) lub protokół (meeting) — manual-first, bez halucynacji.
    Jeśli metadana nie jest podana, używa 'DO UZUPEŁNIENIA'. Każdy brak faktu -> 'DO POTWIERDZENIA'.
    """
    app_cfg = load_config()
    chosen_model = model or app_cfg.llm.enricher_model
    provider = get_llm_provider(app_cfg.llm)

    combined_speech = " ".join(s.speech_text for s in steps if s.speech_text)
    ref_dir = reference_dir or app_cfg.storage.reference_docs_dir
    reference_context = find_relevant_reference_docs(f"{title} {combined_speech}", ref_dir)

    def _v(val: Optional[str]) -> str:
        if val is None or str(val).strip() == "":
            return "DO UZUPEŁNIENIA"
        return str(val).strip()

    meta_client = _v(client)
    meta_process = _v(process)
    meta_module = _v(module)
    meta_env = _v(environment)
    meta_author = _v(author)
    meta_status = document_status if document_status else "DRAFT"

    if mode == "meeting":
        task_desc = (
            "Twoim zadaniem jest zredagowanie protokołu ze spotkania w języku polskim. "
            "Sekcje: podstawy spotkania, uczestnicy (jeśli znani), tematy, decyzje, ustalenia, action items z owner/termin, pytania otwarte, ryzyka, DO POTWIERDZENIA."
        )
    else:
        task_desc = "Twoim zadaniem jest zredagowanie profesjonalnej, zwięzłej instrukcji technicznej/stanowiskowej w języku polskim."

    prompt_lines = [
        "Jesteś doświadczonym specjalistą tworzącym oficjalną dokumentację techniczną i procedury operacyjne.",
        task_desc,
        "",
        "ZASADY REDAKCJI:",
        "1. DOKUMENTACJA MA BYĆ CZYSTA I PRZEJRZYSTA — brak zbędnego żargonu, skupienie na faktach z nagrania.",
        "2. Obiekt 'meta': użyj wyłącznie podanych metadanych. Jeśli jakiejś nie podano, wpisz 'DO UZUPEŁNIENIA'.",
        "3. Obiekt 'intro': dokładnie 1-2 zwięzłe zdania definiujące cel biznesowy operacji / cel spotkania.",
        "4. Dla każdego kroku przygotuj:",
        "   - 'title': krótki, celny tytuł działania.",
        "   - 'description': MAKSYMALNIE 2 konkretne zdania instruktażowe w trybie rozkazującym/bezosobowym.",
        "   - DOKŁADNIE JEDNĄ ramkę wiedzy:",
        "       * 'callout_type': 'warning' (dla ryzyk i ostrzeżeń),",
        "       * 'callout_type': 'tip' (dla wskazówek i dobrych praktyk),",
        "       * 'callout_type': 'quote' (cytat lektora).",
        "5. STATUS DOKUMENTU: Zawsze ustawiaj status na 'DRAFT' lub 'DO WERYFIKACJI'. Nigdy 'FINAL'.",
        "",
        f"METADANE WEJŚCIOWE:",
        f"- Klient: {meta_client}",
        f"- Proces: {meta_process}",
        f"- Moduł: {meta_module}",
        f"- Środowisko: {meta_env}",
        f"- Autor: {meta_author}",
        f"- Status bazowy: {meta_status}",
        "",
        f"TYTUŁ: {title}",
    ]

    if reference_context:
        prompt_lines.append(f"\nKONTEKST DOKUMENTACJI REFERENCYJNEJ:\n{reference_context}\n")

    prompt_lines.append("\nKROKI WYKRYTE Z POTOKU (TIMESTAMPY, TRANSKRYPCJA, OCR):")
    for s in steps:
        ocr_text = extract_frame_ocr(s.frame_path)
        prompt_lines.append(f"\n--- KROK {s.step_number} (czas: {s.start_time:.1f}s - {s.end_time:.1f}s) ---")
        prompt_lines.append(f"Mowa/Lektor: {s.speech_text}")
        if ocr_text:
            prompt_lines.append(f"Tekst z ekranu (OCR):\n{ocr_text[:300]}")

    prompt_lines.append(
        "\nOdpowiedz WYŁĄCZNIE poprawnym obiektem JSON o schemacie:\n"
        "{\n"
        '  "meta": {"client": "...", "process": "...", "module": "...", "environment": "...", "author": "...", "document_status": "DRAFT", "mode": "manual"},\n'
        '  "intro": "1-2 zdania wstępu.",\n'
        '  "steps": [\n'
        '    {"step_number": 1, "title": "...", "description": "...", "callout_type": "warning|tip|quote", "callout_text": "..."}\n'
        "  ]\n"
        "}"
    )

    system_msg = "Jesteś ekspertem dokumentacji technicznej. Odpowiadaj wyłącznie poprawnym obiektem JSON."
    messages = [
        {"role": "system", "content": system_msg},
        {"role": "user", "content": "\n".join(prompt_lines)},
    ]

    try:
        content = provider.chat_completion(
            messages=messages,
            model=chosen_model,
            temperature=0.2,
            max_tokens=2500,
            json_mode=True,
        )
        parsed = robust_json_decode(content)
        return parsed
    except Exception as e:
        print(f"[OSTRZEŻENIE] Błąd podczas wzbogacania kroków przez LLM: {e}")
        return None
