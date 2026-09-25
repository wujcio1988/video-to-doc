"""Bramka QA DRAFT: twarde reguły regex + audyt LLM w chunkach (xKiro, bezpośrednio).

Audyt NIE jest weryfikacją merytoryczną z bazą ERP — status zawsze DRAFT / DO WERYFIKACJI.
Audyt działa w chunkach po ~80 kroków z podwójną próbą: model szybki, potem model
zapasowy. Fail-safe: awaria pojedynczego chunka nie odrzuca dokumentu (uwaga w raporcie),
a całkowita awaria LLM degraduje do twardych reguł regex (zachowanie historyczne).
"""
import json
import os
import re
import urllib.request
from pathlib import Path
from typing import Dict, Any, List, Optional, Tuple

from vtd.enricher import ENRICH_MODEL_DEFAULT, ENRICH_MODEL_FALLBACKS, _call_enrich_model, get_xkiro_token, robust_json_decode
from vtd.fusion import ManualStep

ENV_FILE = Path.home() / ".hermes" / ".env"

AUDIT_CHUNK_SIZE = 80
AUDIT_FALLBACK_MODEL = ENRICH_MODEL_FALLBACKS[0]

_SYSTEM_PROMPT = (
    "Jesteś rygorystycznym audytorem dokumentacji ERP. Zwracasz WYŁĄCZNIE poprawny obiekt JSON, "
    "bez tekstu pobocznego, bez wywołań narzędzi."
)


def _build_audit_user_prompt(title: str, chunk_steps: List[Dict[str, Any]], first: int, last: int) -> str:
    return (
        "Jesteś audytorem spójności DRAFT dokumentu wdrożeniowego ERP (Comarch Optima + CTI Produkcja).\n"
        f"Dokument: {title}\n"
        "NIE masz dostępu do bazy ERP — nie weryfikujesz faktów; wątpliwości oznaczasz.\n\n"
        "SPRAWDŹ PODANE KROKI pod kątem:\n"
        "1) format liczb Optima: '52,0000' = 52 sztuki; popraw błędne zapisy typu '5200' gdy kontekst wskazuje sztuki;\n"
        "2) terminologia: ZP = zlecenie produkcyjne, TH = technologia, RW = rozchód wewnętrzny (surowce są ROZCHODOWANE dokumentem RW — NIGDY nie pisz, że są 'przyjmowane RW'), PW = przyjęcie wyrobu;\n"
        "3) zwięzłość: opis kroku maksymalnie 2 konkretne zdania;\n"
        "4) brak halucynacji: nazwy/kody tylko z treści kroku; wątpliwość → dopisz 'DO POTWIERDZENIA';\n"
        "5) język: polski, tryb bezosobowy lub rozkazujący.\n\n"
        "ZASADY ODPOWIEDZI:\n"
        "- NIE zmieniaj numerów kroków; NIE dodawaj i NIE usuwaj kroków.\n"
        "- Zwróć WSZYSTKIE podane kroki (nawet niezmienione) w liście 'steps' z polami: step_number, title, description.\n"
        "- Uwagi zbiorcze w 'qa_notes' (maksymalnie 10, zwięzłe).\n"
        "- Status zawsze 'DO WERYFIKACJI'.\n\n"
        'Format odpowiedzi (wyłącznie ten JSON): {"qa_status":"DO WERYFIKACJI","qa_notes":["..."],'
        '"steps":[{"step_number":1,"title":"...","description":"..."}]}\n\n'
        f"Kroki do audytu ({first}-{last}):\n" + json.dumps(chunk_steps, ensure_ascii=False, indent=1)
    )


def _audit_chunk(
    model: str, timeout: int, title: str, chunk_steps: List[Dict[str, Any]], first: int, last: int
) -> Tuple[Dict[int, Dict[str, Any]], List[str]]:
    """Pojedynczy chunk audytu wybranym modelem. Zwraca (poprawki wg numeru, uwagi). Podnosi wyjątek przy awarii."""
    user_text = _build_audit_user_prompt(title, chunk_steps, first, last)
    content = _call_enrich_model(model, _SYSTEM_PROMPT, user_text, timeout)
    parsed = robust_json_decode(content)
    if not isinstance(parsed, dict):
        raise ValueError("odpowiedź audytu nie jest obiektem JSON")

    notes: List[str] = []
    for n in parsed.get("qa_notes") or []:
        n = str(n).strip()
        if n:
            notes.append(n)

    corrections: Dict[int, Dict[str, Any]] = {}
    steps_out = parsed.get("steps")
    if isinstance(steps_out, list):
        for st in steps_out:
            if isinstance(st, dict) and isinstance(st.get("step_number"), int):
                corrections[st["step_number"]] = st
    if not corrections:
        raise ValueError("audyt nie zwrócił poprawionych kroków ('steps')")
    return corrections, notes


def _audit_chunk_robust(
    title: str, chunk: List[Dict[str, Any]], first: int, last: int, model: str, timeout: int
) -> Tuple[Dict[int, Dict[str, Any]], List[str], str]:
    """Chunk z podwójną próbą (model szybki → fallback combo). Zwraca (poprawki, uwagi, użyty_model)."""
    attempts = [model] if model == AUDIT_FALLBACK_MODEL else [model, AUDIT_FALLBACK_MODEL]
    errors: List[str] = []
    for m in attempts:
        try:
            corrections, notes = _audit_chunk(m, timeout, title, chunk, first, last)
            return corrections, notes, m
        except Exception as e:
            errors.append(f"{m}: {type(e).__name__}: {e}")
    raise RuntimeError("; ".join(errors)[:400])


def _apply_corrections(enriched_data: Dict[str, Any], chunk_map: Dict[int, Dict[str, Any]]) -> int:
    """Nakłada poprawki title/description na kroki (scalanie po numerze; nie rusza evidence/ramek/klatek)."""
    applied = 0
    for st in enriched_data.get("steps") or []:
        if not isinstance(st, dict):
            continue
        num = st.get("step_number")
        corr = chunk_map.get(num) if isinstance(num, int) else None
        if not corr:
            continue
        changed = False
        for field in ("title", "description"):
            v = corr.get(field)
            if isinstance(v, str) and v.strip():
                v = v.strip()
                if v != str(st.get(field) or "").strip():
                    st[field] = v
                    changed = True
        if changed:
            applied += 1
    return applied


def audit_and_fix_manual(
    enriched_data: Dict[str, Any],
    steps: List[ManualStep],
    title: str,
    model: str = ENRICH_MODEL_DEFAULT,
    timeout: int = 420,
) -> Tuple[Dict[str, Any], List[str], Dict[str, Any]]:
    """
    Lokalny, ograniczony audyt spójności DRAFT — NIE jest weryfikacją merytoryczną z bazą ERP.
    Sprawdza jedynie format liczb, terminologię Comarch Optima/CTI i spójność językową.
    Bez połączenia z bazą ERP nie można zweryfikować faktów — status to zawsze DRAFT / DO WERYFIKACJI.
    Zwraca (poprawione_dane, lista_uwag, audit_info).
    """
    issues_found: List[str] = []
    audit_info: Dict[str, Any] = {
        "model": model,
        "fallback_model": AUDIT_FALLBACK_MODEL,
        "mode": "regex-fallback",
        "chunks_total": 0,
        "chunks_ok": 0,
        "steps_corrected": 0,
        "reason": None,
    }

    # 1. Twarda weryfikacja reguł polskich formatów liczbowych w Optima
    # W Optima '52,0000' oznacza 52 sztuki, a nie 5200 czy 52000!
    data_str = json.dumps(enriched_data, ensure_ascii=False)
    if "5200" in data_str:
        issues_found.append("KOREKTA LICZBOWA: Wykryto błędną interpretację polskiego formatu dziesiętnego Optima '52,0000' jako '5200'. Skorygowano do właściwej wartości: '52 sztuki'.")
        data_str = re.sub(r"\b5200\b(?:\.00)?(?:\s*sztuk|\s*szt)?", "52 sztuki", data_str)
        enriched_data = json.loads(data_str)

    # 2. Audyt LLM w chunkach (odporny na limity modeli; podwójna próba per chunk)
    steps_list = enriched_data.get("steps")
    last_error: Optional[str] = None
    if not isinstance(steps_list, list) or not steps_list:
        last_error = "brak kroków do audytu"
    elif not get_xkiro_token():
        last_error = "brak XKIRO_API_KEY"
    else:
        audit_steps: List[Dict[str, Any]] = []
        for st in steps_list:
            if isinstance(st, dict) and isinstance(st.get("step_number"), int):
                audit_steps.append({
                    "step_number": st["step_number"],
                    "title": str(st.get("title") or ""),
                    "description": str(st.get("description") or ""),
                })
        chunks = [audit_steps[i:i + AUDIT_CHUNK_SIZE] for i in range(0, len(audit_steps), AUDIT_CHUNK_SIZE)]
        audit_info["chunks_total"] = len(chunks)
        print(f"  [QA/CHUNK] {len(audit_steps)} kroków → {len(chunks)} chunków po ≤{AUDIT_CHUNK_SIZE}.")

        merged_corrections: Dict[int, Dict[str, Any]] = {}
        for ci, chunk in enumerate(chunks, 1):
            first = chunk[0]["step_number"]
            last = chunk[-1]["step_number"]
            try:
                corrections, notes, used_model = _audit_chunk_robust(title, chunk, first, last, model, timeout)
                chunk_numbers = {c["step_number"] for c in chunk}
                corrections = {k: v for k, v in corrections.items() if k in chunk_numbers}
                merged_corrections.update(corrections)
                issues_found.extend(notes)
                audit_info["chunks_ok"] += 1
                print(f"  [QA/CHUNK c{ci}/{len(chunks)}] OK ({used_model}) — poprawki: {len(corrections)}, uwagi: {len(notes)}.")
            except Exception as e:
                last_error = f"{type(e).__name__}: {e}"
                issues_found.append(f"Audyt LLM pominięty dla kroków {first}-{last} — wymagana ręczna kontrola tej sekcji. ({last_error})")
                print(f"  [QA/CHUNK c{ci}/{len(chunks)}] pominięty dla kroków {first}-{last} ({last_error}).")

        if audit_info["chunks_ok"] > 0:
            applied = _apply_corrections(enriched_data, merged_corrections)
            audit_info["steps_corrected"] = applied
            audit_info["mode"] = "llm-chunked"
            audit_info["reason"] = None
            print(f"  [QA/CHUNK] Zastosowano poprawki w {applied} krokach.")
        else:
            audit_info["reason"] = last_error

    # 3. Wymuś status DRAFT w meta (jeśli obecne)
    meta = enriched_data.get("meta")
    if isinstance(meta, dict):
        meta["Status"] = "DRAFT / DO WERYFIKACJI"
        meta["Status instrukcji"] = "DRAFT / DO WERYFIKACJI"

    # 4. Degradacja do reguł regex — komunikat zgodny z zachowaniem historycznym
    if audit_info["mode"] == "regex-fallback":
        print(f"[QA GATE] Ostrzeżenie: automatyczny audyt LLM pominięty ({audit_info['reason']}). Zastosowano twarde reguły regex. Status: DRAFT / DO WERYFIKACJI.")

    # Zawsze DRAFT — dodaj ostrzeżenie jeśli brak uwag
    if not issues_found:
        issues_found.append("DRAFT / DO WERYFIKACJI — automatyczna kontrola spójności (bez połączenia z bazą ERP). Wymagana ręczna weryfikacja przez wdrożeniowca.")
    return enriched_data, issues_found, audit_info
