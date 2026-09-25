"""Kuracja treści (etap --curate): wybór kroków-operacji do instrukcji z nagrania.

Agent LLM klasyfikuje każdy krok: keep (operacje/ekrany programu) lub reject (dyskusje,
logistyka wdrożenia, dygresje). Fail-safe (nic nie znika po cichu):
- brak decyzji dla kroku → krok ZOSTAJE,
- awaria chunka → kroki chunka zostają + wzmianka w raporcie,
- awaria wszystkich chunków → kuracja pominięta (materiał bez zmian),
- zbyt agresywna kuracja (poniżej progu) → przerwana, zostają wszystkie kroki.
Po kuracji numeracja kroków jest ciągła 1..M; mapowanie stare→nowe trafia do raportu.
"""
import json
from datetime import datetime, timezone
from typing import Any, Dict, List, Tuple

from vtd.enricher import ENRICH_MODEL_DEFAULT, _call_enrich_model, robust_json_decode
from vtd.fusion import ManualStep
from vtd.qa_verifier import AUDIT_CHUNK_SIZE, AUDIT_FALLBACK_MODEL

CURATE_MIN_KEEP_RATIO = 0.15

_SYSTEM = (
    "Jesteś kuratorem treści instrukcji ERP. Zwracasz WYŁĄCZNIE poprawny obiekt JSON, "
    "bez tekstu pobocznego, bez wywołań narzędzi."
)


def _build_prompt(title: str, chunk: List[Dict[str, Any]], first: int, last: int, scope: str) -> str:
    if scope == "program+context":
        scope_line = "- pokazywane receptury/dane/importy, gdy opisują pracę z programem."
    else:
        scope_line = ("- kontekst pokazywanych danych tylko wtedy, gdy jest niezbędny do wykonania "
                      "operacji w programie.")
    return (
        "Jesteś kuratorem treści DRAFT instrukcji programu ERP (Comarch Optima + CTI Produkcja).\n"
        f"Dokument: {title}\n"
        "Materiał powstał z nagrania spotkania wdrożeniowego (demo programu + rozmowa z klientem).\n"
        "ZADANIE: dla KAŻDEGO kroku zdecyduj: keep (zostaje w instrukcji) czy reject (wypada).\n\n"
        "ZOSTAW (keep):\n"
        "- kroki opisujące operacje w programie: okna, pola, przyciski, kliknięcia, dokumenty (ZP, TH, RW, PW, PZ), raporty, ustawienia, importy;\n"
        "- opisy funkcji demonstrowanych w programie (zasady działania, skutki operacji);\n"
        f"{scope_line}\n\n"
        "ODRZUĆ (reject):\n"
        "- dyskusje organizacyjne, ustalenia i decyzje (należą do protokołu spotkania, nie do instrukcji);\n"
        "- logistykę wdrożenia: instalacje, dostępy, terminy, szkolenia;\n"
        "- szukanie plików poza programem, problemy sprzętowe/połączeniowe, smalltalk;\n"
        "- powtórzenia, pauzy, kuluarowe pytania bez treści operacyjnej.\n\n"
        "ZASADY:\n"
        "- Rozstrzygnij o WSZYSTKICH krokach z listy; przy wątpliwości → keep.\n"
        "- NIE zmieniaj treści kroków; tylko decyzja + krótki powód.\n"
        '- Format odpowiedzi (wyłącznie ten JSON): {"decisions":[{"step_number":1,"keep":true,"reason":"krótko"}],"summary":"1-2 zdania"}\n\n'
        f"Kroki do kuracji ({first}-{last}):\n" + json.dumps(chunk, ensure_ascii=False, indent=1)
    )


def _curate_chunk(
    model: str, timeout: int, title: str, chunk: List[Dict[str, Any]], first: int, last: int, scope: str
) -> Tuple[Dict[int, bool], Dict[int, str], str]:
    """Pojedynczy chunk kuracji. Zwraca (decyzje wg numeru, powody, summary). Podnosi wyjątek przy awarii."""
    user_text = _build_prompt(title, chunk, first, last, scope)
    content = _call_enrich_model(model, _SYSTEM, user_text, timeout)
    parsed = robust_json_decode(content)
    if not isinstance(parsed, dict):
        raise ValueError("odpowiedź kuracji nie jest obiektem JSON")

    dec: Dict[int, bool] = {}
    rea: Dict[int, str] = {}
    for d in parsed.get("decisions") or []:
        if isinstance(d, dict) and isinstance(d.get("step_number"), int) and isinstance(d.get("keep"), bool):
            dec[d["step_number"]] = d["keep"]
            rea[d["step_number"]] = str(d.get("reason") or "").strip()
    if not dec:
        raise ValueError("kuracja nie zwróciła decyzji ('decisions')")
    summary = str(parsed.get("summary") or "").strip()
    return dec, rea, summary


def _curate_chunk_robust(
    title: str, chunk: List[Dict[str, Any]], first: int, last: int, model: str, timeout: int, scope: str
) -> Tuple[Dict[int, bool], Dict[int, str], str, str]:
    """Chunk z podwójną próbą (model szybki → fallback combo). Zwraca (decyzje, powody, summary, użyty_model)."""
    attempts = [model] if model == AUDIT_FALLBACK_MODEL else [model, AUDIT_FALLBACK_MODEL]
    errors: List[str] = []
    for m in attempts:
        try:
            dec, rea, summ = _curate_chunk(m, timeout, title, chunk, first, last, scope)
            return dec, rea, summ, m
        except Exception as e:
            errors.append(f"{m}: {type(e).__name__}: {e}")
    raise RuntimeError("; ".join(errors)[:400])


def curate_steps(
    enriched_data: Dict[str, Any],
    steps: List[ManualStep],
    title: str,
    model: str = ENRICH_MODEL_DEFAULT,
    timeout: int = 420,
    scope: str = "program",
) -> Tuple[Dict[str, Any], List[ManualStep], Dict[str, Any]]:
    """Kuracja treści. Zwraca (dane po kuracji, kroki po kuracji, curation_info).

    Przy awarii / przerwaniu zwraca materiał BEZ ZMIAN (fail-safe).
    """
    info: Dict[str, Any] = {
        "model": model,
        "fallback_model": AUDIT_FALLBACK_MODEL,
        "mode": "skipped",
        "scope": scope,
        "chunks_total": 0,
        "chunks_ok": 0,
        "steps_total": 0,
        "kept": 0,
        "rejected": 0,
        "kept_map": [],
        "rejected_steps": [],
        "failed_ranges": [],
        "summary": "",
        "reason": None,
    }

    esteps = enriched_data.get("steps") if isinstance(enriched_data, dict) else None
    if not isinstance(esteps, list) or not esteps:
        info["reason"] = "brak kroków do kuracji"
        return enriched_data, steps, info

    audit_steps: List[Dict[str, Any]] = []
    for st in esteps:
        if isinstance(st, dict) and isinstance(st.get("step_number"), int):
            audit_steps.append({
                "step_number": st["step_number"],
                "title": str(st.get("title") or ""),
                "description": str(st.get("description") or ""),
            })
    if not audit_steps:
        info["reason"] = "brak kroków z numeracją"
        return enriched_data, steps, info

    chunks = [audit_steps[i:i + AUDIT_CHUNK_SIZE] for i in range(0, len(audit_steps), AUDIT_CHUNK_SIZE)]
    info["chunks_total"] = len(chunks)
    info["steps_total"] = len(audit_steps)
    print(f"  [KURACJA] {len(audit_steps)} kroków → {len(chunks)} chunków po ≤{AUDIT_CHUNK_SIZE}.")

    decisions: Dict[int, bool] = {}
    reasons: Dict[int, str] = {}
    summaries: List[str] = []
    last_error = None
    for ci, chunk in enumerate(chunks, 1):
        first, last = chunk[0]["step_number"], chunk[-1]["step_number"]
        try:
            dec, rea, summ, used_model = _curate_chunk_robust(title, chunk, first, last, model, timeout, scope)
            chunk_numbers = {c["step_number"] for c in chunk}
            for num, keep in dec.items():
                if num in chunk_numbers:
                    decisions[num] = keep
                    reasons[num] = rea.get(num, "")
            if summ:
                summaries.append(summ)
            info["chunks_ok"] += 1
            kept_in_chunk = sum(1 for c in chunk if decisions.get(c["step_number"], True))
            print(f"  [KURACJA c{ci}/{len(chunks)}] OK ({used_model}) — zostaje {kept_in_chunk}/{len(chunk)} kroków.")
        except Exception as e:
            last_error = f"{type(e).__name__}: {e}"
            info["failed_ranges"].append(f"{first}-{last}")
            print(f"  [KURACJA c{ci}/{len(chunks)}] pominięty dla kroków {first}-{last} ({last_error}) — kroki zachowane.")

    if info["chunks_ok"] == 0:
        info["mode"] = "failed"
        info["reason"] = last_error
        return enriched_data, steps, info

    total = len(audit_steps)
    keep_nums = [c["step_number"] for c in audit_steps if decisions.get(c["step_number"], True)]
    kept = len(keep_nums)
    if kept / total < CURATE_MIN_KEEP_RATIO:
        info["mode"] = "aborted-low-keep"
        info["reason"] = (
            f"zachowano {kept}/{total} kroków — poniżej progu {CURATE_MIN_KEEP_RATIO:.0%}; "
            "przerwano dla bezpieczeństwa (materiał bez zmian)."
        )
        print(f"  [KURACJA] PRZERWANA: {info['reason']}")
        return enriched_data, steps, info

    kept_set = set(keep_nums)
    old_to_new: Dict[int, int] = {}
    kept_map: List[Dict[str, int]] = []
    for st in audit_steps:
        num = st["step_number"]
        if num in kept_set:
            old_to_new[num] = len(old_to_new) + 1
            kept_map.append({"old": num, "new": old_to_new[num]})

    by_num: Dict[int, Dict[str, Any]] = {}
    for st in esteps:
        if isinstance(st, dict):
            num2 = st.get("step_number")
            if isinstance(num2, int):
                by_num[num2] = st

    new_esteps: List[Dict[str, Any]] = []
    for st in esteps:
        if not isinstance(st, dict):
            continue
        num2 = st.get("step_number")
        if isinstance(num2, int) and num2 in kept_set:
            st["step_number"] = old_to_new[num2]
            new_esteps.append(st)
    enriched_data["steps"] = new_esteps

    new_steps: List[ManualStep] = []
    if isinstance(steps, list):
        for s in steps:
            old_num = getattr(s, "step_number", None)
            if isinstance(old_num, int) and old_num in kept_set:
                s.step_number = old_to_new[old_num]
                new_steps.append(s)

    rejected_nums = sorted(n for n in (st["step_number"] for st in audit_steps) if n not in kept_set)
    rejected_list: List[Dict[str, Any]] = []
    for n in rejected_nums:
        src = by_num.get(n) or {}
        rejected_list.append({
            "step_number": n,
            "title": str(src.get("title") or ""),
            "reason": reasons.get(n, ""),
        })
    info.update({
        "mode": "llm-chunked",
        "kept": len(new_esteps),
        "rejected": total - len(new_esteps),
        "kept_map": kept_map,
        "rejected_steps": rejected_list,
        "summary": " ".join(summaries)[:1500],
    })
    print(f"  [KURACJA] Zachowano {len(new_esteps)}/{total} kroków (przenumerowano 1..{len(new_esteps)}).")
    return enriched_data, (new_steps if isinstance(steps, list) else steps), info


def render_curation_report(title: str, info: Dict[str, Any]) -> str:
    """Raport kuracji (CURATION_REPORT.md) — pełna audytowalność decyzji keep/reject."""
    mode = info.get("mode")
    mode_txt = {
        "llm-chunked": f"kuracja LLM w chunkach ({info.get('model')}; fallback: {info.get('fallback_model')})",
        "skipped": "pominięta",
        "failed": "nieudana — materiał bez zmian (fail-safe)",
        "aborted-low-keep": "przerwana przez zabezpieczenie (zbyt agresywna) — materiał bez zmian",
    }.get(mode, str(mode))
    lines = [
        f"# Raport kuracji treści: {title}",
        "",
        f"**Status kuracji:** {mode_txt}",
        f"**Zakres:** {info.get('scope')}",
        f"**Wynik:** zachowano {info.get('kept')} z {info.get('steps_total')} kroków (odrzucono {info.get('rejected')})",
        f"**Data:** {datetime.now(timezone.utc).isoformat()}",
        "",
    ]
    if info.get("reason"):
        lines += [f"**Powód/uwagi:** {info['reason']}", ""]
    if info.get("summary"):
        lines += ["## Podsumowanie agenta", "", info["summary"], ""]
    rej = info.get("rejected_steps") or []
    if rej:
        lines += ["## Odrzucone kroki (nie weszły do instrukcji)", ""]
        for r in rej:
            lines.append(f"- **[{r['step_number']}]** {r.get('title', '')} — {r.get('reason', '')}")
        lines.append("")
    kept_map = info.get("kept_map") or []
    if kept_map:
        lines += ["## Numeracja po kuracji (nowy ← stary)", "", ", ".join(f"{k['new']} ← {k['old']}" for k in kept_map), ""]
    failed = info.get("failed_ranges") or []
    if failed:
        lines += ["## Chunki pominięte (kroki zachowane bez oceny)", "", ", ".join(failed), ""]
    lines += [
        "> Dokument pozostaje DRAFT / DO WERYFIKACJI — kuracja to selekcja treści (co należy do instrukcji programu), "
        "nie weryfikacja merytoryczna.",
        "",
    ]
    return "\n".join(lines)
