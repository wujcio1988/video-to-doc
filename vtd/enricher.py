import json
import os
import re
import subprocess
import time
import urllib.request
from pathlib import Path
from typing import List, Dict, Any, Optional
from vtd.fusion import ManualStep

ENV_FILE = Path(os.environ.get("VIDEO_TO_MANUAL_ENV_FILE", str(Path.home() / ".hermes" / ".env")))
ERP_DOCS_DIR = Path(os.environ.get("VIDEO_TO_MANUAL_ERP_DOCS_DIR", "/app/erp-docs"))
# Bezpośrednio xKiro (docs.xkiro.com). OmniRoute/combo pominięte: free-tier zwracał
# HTTP 200 z pustym content (tool_calls) i ucinał kaskadę przed modelem, który działa.
XKIRO_BASE_URL = os.environ.get("VIDEO_TO_MANUAL_XKIRO_URL", "https://api.xkiro.com").rstrip("/")
XKIRO_CHAT_URL = f"{XKIRO_BASE_URL}/v1/chat/completions"
# Cloudflare 1010 blokuje domyślny User-Agent urllib/Pythona.
_XKIRO_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36"
)

# Bezpośrednie modele xKiro (vendor/model), nie nazwy combo OmniRoute.
# gemini-3.8-flash: test 2026-09-23 — 40 kroków JSON w 15 s (reasoning_effort=low).
# claude-sonnet-5: wolniejszy, ale 213× HTTP 200 tego dnia i czysty JSON.
ENRICH_MODEL_DEFAULT = "google/gemini-3.8-flash"
ENRICH_MODEL_FALLBACKS = ["anthropic/claude-sonnet-5"]
# xKiro: żądanie bez streamu ginie po 95 s (docs). Chunk ~80 kroków mieści się poniżej.
ENRICH_MAX_TOKENS = 16000
# xKiro documents a ~95 s upstream request limit.  Never let a caller pass a
# larger value: urllib's timeout is per socket operation, so the hard cap is
# also what prevents a stalled chunk from holding the whole run indefinitely.
ENRICH_REQUEST_TIMEOUT = 90
ENRICH_CHUNK_TIMEOUT = 95

# Chunking dla długich transkryptów (> ~150 kroków / meeting mode).
# Bez tego prompt > 100k tokenów → OmniRoute 504 gateway timeout.
CHUNK_THRESHOLD_STEPS = 120   # powyżej tej liczby kroków dzielimy na chunki
CHUNK_SIZE_STEPS = 80         # rozmiar jednego chunka (z overlapem kontekstu)
CHUNK_OVERLAP_STEPS = 10      # ile kroków z poprzedniego chunka powtórzyć jako kontekst

def get_xkiro_token() -> str:
    if ENV_FILE.exists():
        for line in ENV_FILE.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line.startswith("XKIRO_API_KEY="):
                return line.split("=", 1)[1].strip("'\"")
    return os.environ.get("XKIRO_API_KEY", "")


def get_omniroute_token() -> str:
    """Klucz lokalnej bramy OmniRoute — wyłącznie dla ścieżek wizyjnych (Frame QA, adnotacje).

    Redakcja tekstu (enrich, QA, kuracja) idzie bezpośrednio na xKiro i tego klucza nie używa.
    """
    if ENV_FILE.exists():
        for line in ENV_FILE.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line.startswith("OMNIROUTE_API_KEY="):
                return line.split("=", 1)[1].strip("'\"")
    return os.environ.get("OMNIROUTE_API_KEY", "")

# Artefakty UI/śmieci OCR do odsiania przed wysłaniem do LLM (oszczędność tokenów + czystszy kontekst)
_OCR_JUNK_RE = re.compile(r"^[\s\W_]+$")           # linie bez treści alfanumerycznej (×, □, _, |, ---)
_OCR_MIN_ALNUM = 4                                  # min. znaków alfanumerycznych, by linia była użyteczna

def _clean_ocr_lines(raw: str) -> str:
    """Filtruje śmieci OCR: linie bez treści, artefakty UI, zdublowane nagłówki.
    Zwraca maks. 20 najbardziej informatywnych, unikalnych linii."""
    seen: set = set()
    out: List[str] = []
    for line in raw.splitlines():
        line = line.strip()
        if len(line) < 3:
            continue
        if _OCR_JUNK_RE.match(line):
            continue
        if sum(c.isalnum() for c in line) < _OCR_MIN_ALNUM:
            continue
        key = line.lower()
        if key in seen:            # duplikaty nagłówków kolumn / powtórzone etykiety
            continue
        seen.add(key)
        out.append(line)
        if len(out) >= 20:
            break
    return "\n".join(out)

def extract_frame_ocr(frame_path: Path) -> str:
    if not frame_path.exists():
        return ""
    try:
        res = subprocess.run(
            ["tesseract", str(frame_path), "stdout", "-l", "pol+eng", "--psm", "6"],
            capture_output=True,
            text=True,
            timeout=5
        )
        return _clean_ocr_lines(res.stdout)
    except Exception:
        return ""

def find_relevant_erp_docs(query_text: str, max_chars: int = 3000) -> str:
    matches = []
    cti_docs = ERP_DOCS_DIR / "cti" / "instrukcje-markdown"
    if not cti_docs.exists():
        return ""

    keywords = ["zlecenia", "produkcja", "th", "surowc", "magazyn", "parti"]
    query_lower = query_text.lower()
    
    for md_file in cti_docs.glob("*.md"):
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

def robust_json_decode(text: str) -> Dict[str, Any]:
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```[a-zA-Z]*\n", "", text)
        text = re.sub(r"\n```$", "", text)
    text = text.strip()
    
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
        
    cleaned = text.replace('„', '\\"').replace('”', '\\"')
    # Usuń znaki kontrolne poza spacją
    cleaned = re.sub(r'[\x00-\x1f]+', ' ', cleaned)
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        pass

    # Jeśli ucięło końcówkę JSON-a:
    if not cleaned.endswith("}"):
        try:
            return json.loads(cleaned + '"}')
        except Exception:
            pass
        try:
            return json.loads(cleaned + '"}]}')
        except Exception:
            pass
            
    raise ValueError(f"Niepoprawny format JSON (długość: {len(text)})")


def _call_enrich_model(model: str, system_content: str, user_text: str, timeout: int) -> str:
    """Pojedyncze wywołanie modelu enrichmentu bezpośrednio na xKiro (bez combo).

    Zwraca treść odpowiedzi (bez fence ```). Podnosi wyjątek przy błędzie sieci/modelu
    lub pustej odpowiedzi. reasoning_effort=low: gemini domyślnie medium i zjada budżet
    tokenów na myślenie zamiast na JSON. response_format wymusza obiekt JSON.
    """
    token = get_xkiro_token()
    if not token:
        raise RuntimeError("brak XKIRO_API_KEY")
    # Prefiks xkiro/ to alias OmniRoute — na api.xkiro.com model to vendor/model.
    model_id = model[6:] if model.startswith("xkiro/") else model
    payload = {
        "model": model_id,
        "messages": [
            {"role": "system", "content": system_content},
            {"role": "user", "content": user_text},
        ],
        "temperature": 0.2,
        "max_tokens": ENRICH_MAX_TOKENS,
        "reasoning_effort": "low",
        "response_format": {"type": "json_object"},
    }
    req = urllib.request.Request(
        XKIRO_CHAT_URL,
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {token}",
            "User-Agent": _XKIRO_UA,
            "Accept": "application/json",
        },
    )
    # The xKiro gateway stops non-streaming requests around 95 seconds.  Keep
    # each request below that limit even if a legacy caller supplies 180/420.
    request_timeout = min(max(1, int(timeout)), ENRICH_REQUEST_TIMEOUT)
    with urllib.request.urlopen(req, timeout=request_timeout) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    content = (data["choices"][0]["message"]["content"] or "").strip()
    if content.startswith("```"):
        content = re.sub(r"^```[a-zA-Z]*\n", "", content)
        content = re.sub(r"\n```$", "", content)
    if not content:
        raise ValueError("model zwrócił pustą odpowiedź")
    return content


def _validate_enriched(parsed: Any) -> List[str]:
    """Walidacja strukturalna odpowiedzi enrichera. Pusta lista == wynik poprawny."""
    errors: List[str] = []
    if not isinstance(parsed, dict):
        return ["odpowiedź nie jest obiektem JSON"]
    if not str(parsed.get("intro", "")).strip():
        errors.append("brak niepustego 'intro'")
    steps = parsed.get("steps")
    if not isinstance(steps, list) or not steps:
        errors.append("brak niepustej listy 'steps'")
        return errors
    for i, st in enumerate(steps):
        if not isinstance(st, dict):
            errors.append(f"steps[{i}] nie jest obiektem")
            continue
        if st.get("step_number") is None:
            errors.append(f"steps[{i}]: brak 'step_number'")
        if not str(st.get("title", "")).strip():
            errors.append(f"steps[{i}]: pusty 'title'")
        if not str(st.get("description", "")).strip():
            errors.append(f"steps[{i}]: pusty 'description'")
    return errors


def enrich_steps_with_llm(
    title: str,
    steps: List[ManualStep],
    model: str = "erp-manual",
    timeout: int = 180,
    client: Optional[str] = None,
    process: Optional[str] = None,
    module: Optional[str] = None,
    environment: Optional[str] = None,
    author: Optional[str] = None,
    document_status: str = "DRAFT",
    mode: str = "manual",
) -> Optional[Dict[str, Any]]:
    """
    Redaguje zwięzłą instrukcję (manual) lub protokół (meeting) — manual-first, bez halucynacji.
    Jeśli metadane nie podano, używa 'DO UZUPEŁNIENIA'. Każdy brak faktu -> 'DO POTWIERDZENIA'.
    Źródła: narration/transcript/OCR/reference material/model suggestion — muszą być oznaczone.
    """
    combined_speech = " ".join(s.speech_text for s in steps if s.speech_text)
    erp_context = find_relevant_erp_docs(f"{title} {combined_speech}")

    # Metadane — nigdy nie wymyślaj wartości, brak = DO UZUPEŁNIENIA
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

    # Dla kompatybilności zachowaj informację o trybie
    if mode == "meeting":
        task_desc = "Twoim zadaniem jest zredagowanie protokołu ze spotkania (nie instrukcji) w języku polskim — sekcje: podstawy spotkania, uczestnicy (jeśli znani), tematy, decyzje, ustalenia, action items z owner/termin, pytania otwarte, ryzyka, DO POTWIERDZENIA."
    else:
        task_desc = "Twoim zadaniem jest zredagowanie perfekcyjnej, zwięzłej instrukcji stanowiskowej w języku polskim."

    prompt_lines = [
        "Jesteś starszym konsultantem Comarch ERP Optima i autorem oficjalnych kart stanowiskowych ISO/ERP.",
        task_desc,
        "",
        "ZŁOTE ZASADY REDAKCJI (STANDARD 'MNIEJ ZNACZY WIĘCEJ'):",
        "1. DOKUMENTACJA MA BYĆ CZYSTA I PRZEJRZYSTA — NIE generuj żadnych tabel parametrów ani list bazodanowych wewnątrz kroków!",
        "2. Obiekt 'meta': użyj wyłącznie podanych metadanych. Jeśli jakiejś nie podano, wpisz 'DO UZUPEŁNIENIA' — NIGDY nie wymyślaj nazw klienta, produktu ani środowiska.",
        "3. Obiekt 'intro': dokładnie 1-2 zwięzłe zdania definiujące cel biznesowy operacji / cel spotkania.",
        "4. Dla każdego kroku/elementu przygotuj:",
        "   - 'title': krótki, celny tytuł działania.",
        "   - 'description': MAKSYMALNIE 2 konkretne zdania instruktażowe w trybie rozkazującym/bezosobowym.",
        "   - DOKŁADNIE JEDNĄ wyrazistą ramkę wiedzy biznesowej:",
        "       * 'callout_type': 'warning' (dla statusów, blokad bufora, ryzyk),",
        "       * 'callout_type': 'tip' (dla reguł CTI, technologii, skrótów),",
        "       * 'callout_type': 'quote' (cytat lektora).",
        "       * 'callout_title': tytuł ramki.",
        "       * 'callout_text': zwięzła treść.",
        "5. ŹRÓDŁA — każdy fakt oznacz jednym z: narration/transcript/OCR/reference material/model suggestion. Nie dopisuj faktów spoza materiału. Brak danych oznacz jako 'DO POTWIERDZENIA' / 'DO UZUPEŁNIENIA'.",
        "6. Status dokumentu to DRAFT — nigdy FINAL. QA to tylko draft do weryfikacji, nie weryfikacja merytoryczna z bazą ERP.",
        "",
        "Format wyjściowy: Czysty obiekt JSON (bez znaczników ```json):",
        "{",
        '  "intro": "Prawidłowe zarejestrowanie zlecenia produkcyjnego dla partii... (źródło: narration/transcript)",',
        '  "meta": {',
        f'    "Klient": "{meta_client}",',
        f'    "Proces": "{meta_process}",',
        f'    "Moduł ERP": "{meta_module}",',
        f'    "Środowisko": "{meta_env}",',
        f'    "Autor": "{meta_author}",',
        f'    "Status instrukcji": "{meta_status}"',
        "  },",
        '  "steps": [',
        "    {",
        '      "step_number": 1,',
        '      "title": "Tytuł działania",',
        '      "description": "Opis operacji (max 2 konkretne zdania) [źródło: transcript/OCR]",',
        '      "callout_type": "warning|tip|quote",',
        '      "callout_title": "Tytuł ramki",',
        '      "callout_text": "Treść ramki [źródło: narration/model suggestion]",',
        '      "source": "narration/transcript/OCR/reference material/model suggestion",',
        '      "evidence": {"frame": "scene_0001.png", "timestamp": "0.0s - 5.0s"}',
        "    }",
        "  ],",
        '  "decisions": [], "agreements": [],',
        '  "action_items": [{"task": "...", "owner": "DO POTWIERDZENIA", "due": "DO POTWIERDZENIA", "source": "transcript", "evidence": {"timestamp": "..."}}],',
        '  "open_questions": [], "risks": []',
        "}",
        "",
        "PRZYKŁAD WZORCOWY (styl i długość opisu — NIE kopiuj treści):",
        '  "title": "Dodaj technologię do wyrobu",',
        '  "description": "Otwórz listę technologii i kliknij Dodaj (lub Duplikuj dla istniejącej). Wskaż kod wyrobu ikoną plusa i podaj ilość wyrobów.",',
        "  (ŹLE — rozwlekłe i bezosobowe: 'Użytkownik powinien rozważyć możliwość...'. DOBRZE — krótko, tryb rozkazujący, jedna myśl na zdanie.)",
        "",
        f"Wiedza ERP z bazy (reference material):\n{erp_context if erp_context else 'Brak dodatkowych materiałów referencyjnych — oznacz braki jako DO POTWIERDZENIA.'}\n",
        f"Metadane wejściowe: klient={meta_client}, proces={meta_process}, moduł={meta_module}, środowisko={meta_env}, autor={meta_author}, status={meta_status}, tryb={mode}",
        f"Temat: {title}\n",
        "Materiały z klatek wideo i mowy (narration/transcript/OCR):"
    ]

    for s in steps:
        speech = s.speech_text if s.speech_text else "Brak mowy lektora."
        ocr = extract_frame_ocr(s.frame_path)
        prompt_lines.append(f"\n--- Krok {s.step_number} [{s.start_time:.1f}s - {s.end_time:.1f}s] [źródło: narration/transcript] [klatka: {s.frame_path.name}] ---")
        prompt_lines.append(f"Głos lektora (transcript): '{speech}'")
        if ocr:
            prompt_lines.append(f"Elementy z ekranu (OCR):\n{ocr}")
        else:
            prompt_lines.append("Elementy z ekranu (OCR): DO POTWIERDZENIA — brak OCR")

    system_content = "Jesteś ekspertem zwięzłej dokumentacji ERP ISO. Odpowiadaj wyłącznie poprawnym obiektem JSON bez żadnego dodatkowego tekstu."

    # --- CHUNKING: przy >CHUNK_THRESHOLD_STEPS krokach dzielimy na okna i konsolidujemy ---
    if len(steps) > CHUNK_THRESHOLD_STEPS:
        return _enrich_chunked(
            title=title, steps=steps, model=model, timeout=timeout,
            erp_context=erp_context, meta_client=meta_client, meta_process=meta_process,
            meta_module=meta_module, meta_env=meta_env, meta_author=meta_author,
            meta_status=meta_status, mode=mode, system_content=system_content,
        )

    user_text = "\n".join(prompt_lines)

    # Odporność: primary -> chain fallbacków; przy każdym modelu 1 próba + 1 korekta po błędach.
    # Walidacja strukturalna zamiast cichego fallbacku na None.
    models_to_try = [model] + [m for m in ENRICH_MODEL_FALLBACKS if m != model]
    last_errors: List[str] = []
    for m in models_to_try:
        attempt_text = user_text
        for attempt in (1, 2):
            try:
                content = _call_enrich_model(m, system_content, attempt_text, timeout)
            except Exception as e:
                print(f"[ENRICHER] {m}: błąd wywołania (próba {attempt}): {e}")
                if attempt == 1:
                    continue  # druga próba tym samym modelem (puste odpowiedzi bywają przejściowe)
                break  # przejdź do kolejnego modelu
            try:
                parsed = robust_json_decode(content)
            except Exception as e:
                last_errors = [f"niepoprawny JSON: {e}"]
                print(f"[ENRICHER] {m}: {last_errors[0]} (próba {attempt})")
                attempt_text = (
                    user_text
                    + "\n\nUWAGA: poprzednia odpowiedź nie była poprawnym JSON-em. "
                    "Zwróć WYŁĄCZNIE poprawny obiekt JSON."
                )
                continue
            errors = _validate_enriched(parsed)
            if not errors:
                via = "fallback" if m != model else ("korekta" if attempt > 1 else "pierwsza próba")
                print(f"[ENRICHER] Sukces ({m}, {via}).")
                return parsed
            last_errors = errors
            print(f"[ENRICHER] {m}: walidacja nie przeszła (próba {attempt}): {'; '.join(errors[:4])}")
            attempt_text = (
                user_text
                + "\n\nUWAGA: poprzednia odpowiedź miała błędy: "
                + "; ".join(errors[:6])
                + ". Popraw i zwróć PEŁNY poprawny obiekt JSON (wszystkie kroki, bez skrótów)."
            )
    print(f"[ENRICHER] Nie udało się uzyskać poprawnej odpowiedzi ({'; '.join(last_errors[:4]) if last_errors else 'brak udanych prób'}).")
    return None


def _build_step_block(s: ManualStep) -> str:
    """Jeden blok materiału źródłowego dla kroku (transkrypt + OCR)."""
    speech = s.speech_text if s.speech_text else "Brak mowy lektora."
    ocr = extract_frame_ocr(s.frame_path)
    lines = [
        f"\n--- Krok {s.step_number} [{s.start_time:.1f}s - {s.end_time:.1f}s] [źródło: narration/transcript] [klatka: {s.frame_path.name}] ---",
        f"Głos lektora (transcript): '{speech}'",
    ]
    lines.append(f"Elementy z ekranu (OCR):\n{ocr}" if ocr else "Elementy z ekranu (OCR): DO POTWIERDZENIA — brak OCR")
    return "\n".join(lines)


def _enrich_chunked(
    *,
    title: str,
    steps: List[ManualStep],
    model: str,
    timeout: int,
    erp_context: str,
    meta_client: str, meta_process: str, meta_module: str,
    meta_env: str, meta_author: str, meta_status: str,
    mode: str, system_content: str,
) -> Optional[Dict[str, Any]]:
    """Redaguje długi transkrypt w oknach CHUNK_SIZE_STEPS i konsoliduje wyniki.

    Bez tego >~120 kroków = prompt >100k tokenów → OmniRoute 504. Każdy chunk
    dostaje overlap CHUNK_OVERLAP_STEPS z poprzednika dla spójności narracji.
    Fallthrough: jeśli jeden chunk padnie na całym chainie modeli, dokument jest
    nadal zwracany z brakami oznaczonymi DO POTWIERDZENIA (manual-first kontrakt).
    """
    n_chunks = (len(steps) + CHUNK_SIZE_STEPS - 1) // CHUNK_SIZE_STEPS
    print(f"[ENRICHER/CHUNK] {len(steps)} kroków → {n_chunks} chunków po ≤{CHUNK_SIZE_STEPS} (overlap {CHUNK_OVERLAP_STEPS}).")

    models_to_try = [model] + [m for m in ENRICH_MODEL_FALLBACKS if m != model]
    # A chunk has one bounded budget, rather than allowing retries and models
    # to multiply into an unbounded scheduler stall.
    chunk_timeout = min(max(1, int(timeout)), ENRICH_CHUNK_TIMEOUT)
    all_steps_out: List[Dict[str, Any]] = []
    intro_parts: List[str] = []
    failed_chunks: List[int] = []
    meeting_sections: Dict[str, List[Any]] = {
        "decisions": [], "agreements": [], "action_items": [],
        "open_questions": [], "risks": [],
    }

    for ci in range(n_chunks):
        lo = ci * CHUNK_SIZE_STEPS
        hi = min(lo + CHUNK_SIZE_STEPS, len(steps))
        ctx_lo = max(0, lo - CHUNK_OVERLAP_STEPS)
        chunk_steps = steps[ctx_lo:hi]
        is_first = (ci == 0)

        # Kontekst nagłówkowy identyczny jak przy pełnym runie, plus informacja o części
        header_lines = [
            "Jesteś starszym konsultantem Comarch ERP Optima i autorem oficjalnych kart stanowiskowych ISO/ERP.",
            ("Twoim zadaniem jest zredagowanie protokołu ze spotkania (nie instrukcji) w języku polskim — sekcje: podstawy spotkania, uczestnicy (jeśli znani), tematy, decyzje, ustalenia, action items z owner/termin, pytania otwarte, ryzyka, DO POTWIERDZENIA."
             if mode == "meeting" else
             "Twoim zadaniem jest zredagowanie perfekcyjnej, zwięzłej instrukcji stanowiskowej w języku polskim."),
            "",
            f"To jest CZĘŚĆ {ci+1}/{n_chunks} materiału źródłowego. Opracuj TYLKO kroki należące do tej części "
            "(numery kroków zachowaj dokładnie takie, jak w materiale). Nie podsumowuj całości — redakcja częściowa.",
            "ZASADY: zwięzłość (max 2 zdania opisu na krok), jedno źródło na fakt (narration/transcript/OCR/reference material/model suggestion), brak danych = DO POTWIERDZENIA / DO UZUPEŁNIENIA, status DRAFT.",
            "",
            "Format wyjściowy: Czysty obiekt JSON (bez ```json):",
            '{"intro": "...", "steps": [{"step_number": N, "title": "...", "description": "...",',
            '"callout_type": "warning|tip|quote", "callout_title": "...", "callout_text": "...",',
            '"source": "...", "evidence": {"frame": "...", "timestamp": "..."}}],',
            '"decisions": [], "agreements": [],',
            '"action_items": [{"task": "...", "owner": "DO POTWIERDZENIA", "due": "DO POTWIERDZENIA", "source": "transcript", "evidence": {"timestamp": "..."}}],',
            '"open_questions": [], "risks": []}',
            "Każdy action_item ma: {" + '"task": "...", "owner": "DO POTWIERDZENIA", "due": "DO POTWIERDZENIA", "source": "transcript", "evidence": {"timestamp": "..."}' + "}. Nie twórz action itemu z przypuszczenia.",
            "",
            f"Wiedza ERP z bazy (reference material):\n{erp_context if erp_context else 'Brak dodatkowych materiałów referencyjnych.'}\n",
            f"Metadane wejściowe: klient={meta_client}, proces={meta_process}, moduł={meta_module}, środowisko={meta_env}, autor={meta_author}, status={meta_status}, tryb={mode}",
            f"Temat: {title}\n",
            "Materiały z klatek wideo i mowy (narration/transcript/OCR):",
        ]
        user_text = "\n".join(header_lines) + "\n" + "\n".join(_build_step_block(s) for s in chunk_steps)

        parsed_chunk: Optional[Dict[str, Any]] = None
        last_errors: List[str] = []
        chunk_deadline = time.monotonic() + chunk_timeout
        for m in models_to_try:
            if time.monotonic() >= chunk_deadline:
                last_errors = ["przekroczono budżet czasu chunka"]
                break
            attempt_text = user_text
            for attempt in (1, 2):
                if time.monotonic() >= chunk_deadline:
                    last_errors = ["przekroczono budżet czasu chunka"]
                    break
                try:
                    content = _call_enrich_model(m, system_content, attempt_text, min(chunk_timeout, max(1, int(chunk_deadline - time.monotonic()))))
                except Exception as e:
                    print(f"[ENRICHER/CHUNK c{ci+1}] {m}: błąd wywołania (próba {attempt}): {e}")
                    if attempt == 1:
                        continue
                    break
                try:
                    parsed = robust_json_decode(content)
                except Exception as e:
                    last_errors = [f"niepoprawny JSON: {e}"]
                    attempt_text = user_text + "\n\nUWAGA: poprzednia odpowiedź nie była poprawnym JSON-em. Zwróć WYŁĄCZNIE poprawny obiekt JSON."
                    continue
                errs = _validate_enriched(parsed)
                if not errs:
                    parsed_chunk = parsed
                    via = "fallback" if m != model else ("korekta" if attempt > 1 else "pierwsza próba")
                    print(f"[ENRICHER/CHUNK c{ci+1}/{n_chunks}] Sukces ({m}, {via}) — {len(parsed.get('steps',[]))} kroków.")
                    break
                last_errors = errs
                attempt_text = (user_text + "\n\nUWAGA: poprzednia odpowiedź miała błędy: "
                                + "; ".join(errs[:6])
                                + ". Popraw i zwróć PEŁNY poprawny obiekt JSON (wszystkie kroki tej części, bez skrótów).")
            if parsed_chunk is not None:
                break
        if parsed_chunk is None:
            failed_chunks.append(ci + 1)
            print(f"[ENRICHER/CHUNK c{ci+1}] CAŁKOWITA PORAŻKA na chainie modeli ({'; '.join(last_errors[:3]) if last_errors else 'brak prób'}) — część pominięta.")
            continue
        if is_first and str(parsed_chunk.get("intro", "")).strip():
            intro_parts.append(str(parsed_chunk["intro"]).strip())
        for st in parsed_chunk.get("steps", []):
            if isinstance(st, dict):
                all_steps_out.append(st)
        # Meeting facts must survive chunking; dedupe is done after all chunks.
        if mode == "meeting":
            for key in meeting_sections:
                values = parsed_chunk.get(key, [])
                if isinstance(values, list):
                    meeting_sections[key].extend(v for v in values if isinstance(v, (dict, str)))

    if not all_steps_out:
        print("[ENRICHER/CHUNK] Żaden chunk nie zwrócił kroków — enrich zwraca None.")
        return None

    # Deduplikacja po step_number (overlap powtarza kroki kontekstowe)
    seen_nums: set = set()
    deduped: List[Dict[str, Any]] = []
    for st in sorted(all_steps_out, key=lambda x: x.get("step_number") or 0):
        num = st.get("step_number")
        if num in seen_nums:
            continue
        seen_nums.add(num)
        deduped.append(st)

    def _dedupe_values(values: List[Any]) -> List[Any]:
        seen = set()
        result_values = []
        for value in values:
            if isinstance(value, dict):
                marker = (value.get("task") or value.get("decision") or value.get("text") or value.get("question") or value.get("risk") or str(value))
            else:
                marker = str(value)
            marker = " ".join(str(marker).lower().split())
            if marker and marker not in seen:
                seen.add(marker)
                result_values.append(value)
        return result_values

    result: Dict[str, Any] = {
        "intro": " ".join(intro_parts) if intro_parts else "DO POTWIERDZENIA — wprowadzenie niedostępne.",
        "meta": {
            "Klient": meta_client, "Proces": meta_process, "Moduł ERP": meta_module,
            "Środowisko": meta_env, "Autor": meta_author, "Status instrukcji": meta_status,
        },
        "steps": deduped,
        "enricher_status": "PARTIAL" if failed_chunks else "COMPLETE",
        "enricher_warning": {
            "code": "ENRICHER_WARNING",
            "failed_chunks": failed_chunks,
            "message": "Nie wszystkie części zostały wzbogacone; wymagają ręcznej weryfikacji."
        } if failed_chunks else None,
        # Meeting facts are aggregated from chunks, never discarded during consolidation.
        "decisions": _dedupe_values(meeting_sections["decisions"]),
        "agreements": _dedupe_values(meeting_sections["agreements"]),
        "action_items": _dedupe_values(meeting_sections["action_items"]),
        "open_questions": _dedupe_values(meeting_sections["open_questions"]),
        "risks": _dedupe_values(meeting_sections["risks"]),
    }
    total_expected = len(steps)
    coverage = len(deduped) / total_expected if total_expected else 0
    note = ""
    if failed_chunks:
        note = f" [UWAGA: {len(failed_chunks)}/{n_chunks} chunków nie zostało zredagowanych: części {failed_chunks}; braki oznacz DO POTWIERDZENIA przy ręcznej weryfikacji.]"
    print(f"[ENRICHER/CHUNK] Konsolidacja: {len(deduped)}/{total_expected} kroków (pokrycie {coverage:.0%}).{note}")
    return result
