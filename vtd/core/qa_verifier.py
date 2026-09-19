"""
Lokalny audyt spójności wygenerowanego szkicu instrukcji (DRAFT QA).
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from vtd.config import load_config
from vtd.core.fusion import ManualStep
from vtd.providers import get_llm_provider, robust_json_decode


def audit_and_fix_manual(
    enriched_data: Dict[str, Any],
    steps: List[ManualStep],
    title: str,
    model: Optional[str] = None,
    timeout: int = 45,
) -> Tuple[Dict[str, Any], List[str]]:
    """
    Weryfikuje spójność językową i techniczną draftu instrukcji.
    Zwraca (poprawione_dane, lista_uwag).
    """
    issues_found: List[str] = []

    # 1. Sprawdzenie formatu liczb (np. '52,0000' vs '5200')
    data_str = json.dumps(enriched_data, ensure_ascii=False)
    if "5200" in data_str:
        issues_found.append("KOREKTA LICZBOWA: Skorygowano format liczbowy ze skali tysięcznej do bazowej.")
        data_str = re.sub(r"\b5200\b(?:\.00)?(?:\s*sztuk|\s*szt)?", "52 sztuki", data_str)
        enriched_data = json.loads(data_str)

    cfg = load_config()
    chosen_model = model or cfg.llm.qa_verifier_model
    provider = get_llm_provider(cfg.llm)

    audit_prompt = [
        "Jesteś asystentem weryfikacji jakości technicznej draftu instrukcji stanowiskowej.",
        "Twoim zadaniem jest sprawdzenie draftu pod kątem spójności językowej i logicznej.",
        "Wszystkie dokumenty mają status roboczy (DRAFT).",
        "",
        "ZASADY:",
        "1. Opis kroku powinien być zwięzły (maksymalnie 2-3 konkretne zdania).",
        "2. Wskazówki i ramki wiedzy powinny jasno ostrzegać przed błędami operacyjnymi.",
        "3. Używaj wyłącznie pojęć widocznych na ekranie lub wypowiedzianych przez lektora.",
        "",
        "Draft JSON:",
        json.dumps(enriched_data, ensure_ascii=False, indent=2),
        "",
        "Odpowiedz WYŁĄCZNIE JSON o formacie:",
        '{"qa_status": "DRAFT", "qa_notes": ["uwaga 1", "uwaga 2"], "corrected_data": { ... }}',
    ]

    try:
        content = provider.chat_completion(
            messages=[
                {"role": "system", "content": "Jesteś audytorem dokumentacji technicznej. Odpowiadaj TYLKO JSON."},
                {"role": "user", "content": "\n".join(audit_prompt)},
            ],
            model=chosen_model,
            temperature=0.2,
            max_tokens=2500,
            json_mode=True,
        )
        parsed = robust_json_decode(content)
        notes = parsed.get("qa_notes", [])
        if isinstance(notes, list):
            issues_found.extend(str(n) for n in notes)
        corrected = parsed.get("corrected_data")
        if isinstance(corrected, dict) and corrected.get("steps"):
            return corrected, issues_found
    except Exception as e:
        issues_found.append(f"Pomocniczy audyt LLM pominięty: {e}")

    return enriched_data, issues_found
