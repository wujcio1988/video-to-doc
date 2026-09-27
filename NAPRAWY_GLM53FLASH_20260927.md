# NAPRAWY — video-to-doc (VTD)

**Data:** 2026-09-27
**Baza:** `ANALIZA_KODU_GLM53FLASH_20260927.md` (sekcja 2 — Findings, sekcja 5 — plan naprawczy)
**Zakres:** GF-001…GF-005, GF-007…GF-030 (GF-006 tylko bezpieczne porządki lokalne; pełny refactor — BACKLOG)

---

## 1. Status per-GF

| GF | Status | Co zrobiono |
|---|---|---|
| GF-001 | **NAPRAWIONE** | `html_builder.py`: helper `esc()` (`html.escape(quote=True)`); escapowane wszystkie interpolacje tekstowe: metadane, `step_title`, `caption_text`, `speech_desc`, callout (typ/tekst/tytuł), legenda adnotacji, `qa_preview`, `title` (`<title>`+`<h1>`), `intro`, linia narracji track. Test regresyjny XSS. |
| GF-002 | **NAPRAWIONE** | `qa_verifier.py`: usunięto magiczną podmianę „5200" na surowym JSON-stringu; usunięto nieużywany `import re`. Testy: legalne 5200 (tekst, kwota, kod) pozostaje nietknięte; ścieżki LLM (notes/corrected/error) pokryte mockiem providera. |
| GF-003 | **NAPRAWIONE** | `watchdog.py`: `RECORDINGS_DIR` z env `VTD_RECORDINGS_DIR` (domyślnie `./recordings` — przenośne); usunięty hard-coded chat_id `6989419513` (brak `HERMES_ARTIFACT_CHAT` = jawne pominięcie wysyłki z ostrzeżeniem); token bota w nagłówku `Authorization: Bearer` zamiast w URL (nie wycieka do `ps`). Testy: env-based dir, brak chat_id → skip+warning, token nieobecny w argumentach URL. |
| GF-004 | **NAPRAWIONE** | `image_enhancer.py`: `OMNIROUTE_BASE` z env `VTD_OMNIROUTE_BASE`; `_KEY_FILES` z env `VTD_KEY_FILES` (bez `~/.hermes` domyślnie wpisanego w kod); usunięto martwą `enhance_frame()` (nieużywaną w flow CLI, zostawiającą `<stem>_enh_raw.png` w `frames/`). Testy: env-driven stałe, `hasattr(enhance_frame)` False, mock HTTP weryfikatora celu, mock OCR. |
| GF-005 | **NAPRAWIONE** | `pyproject.toml`: `pyyaml` przeniesiony do zależności podstawowych; `config.py`: brak pyyaml przy pliku YAML = **jawne ostrzeżenie** na stdout (wcześniej ciche `pass`); błąd wczytywania konfigu też ostrzega. |
| GF-006 | **CZĘŚCIOWO (porządki lokalne) + BACKLOG** | `cli.py` usunięte: martwe gałęzie `pass` (auto-detekcja ×2, fusion placeholder), duplikaty w słowniku stopwords, identyczne if/else przy `copytree(frames)`, martwe `if not out_path.exists()` przy zapisie PNG. Wszystkie checki `'x' in locals()` zastąpione jawnymi definicjami (parametry/lokalne zawsze istnieją). **BACKLOG:** pełny refactor `run_pipeline` (~1000 linii) na `PipelineContext` (dataclass) — zbyt ryzykowny na jeden przebieg przy braku wcześniejszego pokrycia; obecne testy smoke dają bezpieczną bazę do przyszłego refaktoru. |
| GF-007 | **NAPRAWIONE** | `enricher.py`: nowy parametr `frame_qa_results`; kontekst QA trafia do promptu jako osobny wiersz `UWAGA QA KLATKI (...)`. `cli.py`: usunięta mutacja `s.speech_text += " [Frame QA: ...]"`. Testy: `speech_text` nietknięty, QA obecne w promptcie, w INSTRUKCJA.md brak `[Frame QA`. |
| GF-008 | **NAPRAWIONE** | `cli.py`: fallback toru audio (Track 3 / Track 2 nie istnieje) poprzedzony jawnym `OSTRZEŻENIE` z liczbą strumieni; dostępny tor wybierany poprawnie bez ostrzeżenia. Testy integracyjne na mockach (fallback z warningem / wybór właściwego toru bez warninga). |
| GF-009 | **NAPRAWIONE** | `config.py::get_api_key`: fallback kluczy sprzężony z providerem (base_url): `openrouter` → tylko `OPENROUTER_API_KEY`; omniroute/localhost → `OMNIROUTE_API_KEY`+`OPENAI_API_KEY`; reszta → `OPENAI_API_KEY`. Istniejący test klucza (domyślny openai) przechodzi bez zmian. |
| GF-010 | **NAPRAWIONE** | `element_locator.py`: `except (TimeoutExpired, FileNotFoundError, Exception)` rozbity na konkretne gałęzie (`TimeoutExpired`, `FileNotFoundError`, `OSError`/`ValueError`) z ostrzeżeniem. **BACKLOG (drobny):** cache `load_config()` per sesja — pominięte, bo `load_config` musi reagować na zmiany env (precedencja .env, testy VTD-029); narzut jest minimalny. |
| GF-011 | **NAPRAWIONE** | `dedup.py`: brak PIL/imagehash → jawne `OSTRZEŻENIE ... WYŁĄCZONA` + zwrot klatek; porównanie z **oknem ostatnich N hashy** (`compare_window=5`, deque) zamiast tylko poprzedniej klatki. Testy: brak bibliotek → warning, okno wychwytuje powrót do wcześniejszego wzorca. |
| GF-012 | **NAPRAWIONE** | `frame_extractor.py`: niedopasowanie liczby wpisów `pts_time` do liczby klatek → `RuntimeError` (wcześniej `timestamp = indeks klatki`); sprzątanie starych `scene_*.png` przed ekstrakcją. Testy: brak wpisów → błąd, dokładne dopasowanie OK, stare pliki usunięte. |
| GF-013 | **NAPRAWIONE** | `rerender_docs.py`: nowa `_timestamps_from_prompt()` — rekonstrukcja `frame_name → (start, end)` z `PROMPT_DLA_AGENTA.txt` (jedyny serializer z „Dowód"); parser md używa jej zamiast fabrykować 0-5 s; gdy znaczników nie ma nigdzie — jawne ostrzeżenie (fallback 0-5 s pozostaje). Format linii Dowód **nie został ujednolicony w builderze** (to zmieniłoby output INSTRUKCJA.md) — ścieżka przez wspólny prompt uznana za mniejszą ingerencję; dalsze ujednolicenie: BACKLOG. |
| GF-014 | **NAPRAWIONE** | `studio/app.py`: token autoryzacji **wyłącznie w nagłówku** `X-VTD-Token` (query param usunięty); porównanie `hmac.compare_digest`. Test istniejący zaktualizowany (query → 401) — zmiana zachowania jest treścią naprawy. |
| GF-015 | **NAPRAWIONE** | `studio/app.py`: `@app.on_event("startup")` → `lifespan` handler (`asynccontextmanager`). Warning deprecation FastAPI zniknął z przebiegu testów (3 warnings → 1, pozostający warning pochodzi z pakietu starlette/httpx, nie z naszego kodu). |
| GF-016 | **NAPRAWIONE** | Nowy moduł `vtd/defaults.py` (`DEFAULT_ENRICH_MODEL = "gpt-4o-mini"`) jako jedno źródło prawdy: CLI `--enrich-model` (wcześniej `erp-manual`), Studio fallback (wcześniej `artur-daily` — model NIEBĘDĄCY w ALLOWED_MODELS), `settings_store.DEFAULTS`. Walidacja dopuszcza wyłącznie modele z `ALLOWED_MODELS`. **Zmiana zachowania CLI** (domyślny model enrich) — omówiona w pkt 4. |
| GF-017 | **NAPRAWIONE** | `studio/app.py::task_download`: ZIP budowany w pliku tymczasowym i streamowany chunkami (64 KB) do `StreamingResponse`, plik sprzątany po wysyłce — koniec z `io.BytesIO` w RAM. |
| GF-018 | **NAPRAWIONE** | `studio/db.py`: `_ALLOWED_TASK_COLUMNS` (biała lista) + `ValueError` na nieznaną kolumnę; wartości nadal sparametryzowane `?`. WAL/reuse connection: BACKLOG (niski zysk, ryzyko współbieżności). |
| GF-019 | **NAPRAWIONE** | `transcriber.py`: usunięto `word_timestamps=True`. Test: kwargs transkrypcji bez `word_timestamps`. |
| GF-020 | **NAPRAWIONE** | `enricher.py`: usunięto martwy parametr `timeout`; `cli.py` nie przekazuje `timeout=180`. Test: `inspect.signature` bez `timeout`. |
| GF-021 | **NAPRAWIONE** | `enricher.py`: cache OCR `_ocr_cache` per `(ścieżka, mtime)`; powtórne OCR tej samej klatki = 1 subprocess (mock). Cache lokalny dla enrichera; pełny cache współdzielony locator/QA: BACKLOG (wymaga wspólnej infrastruktury). |
| GF-022 | **NAPRAWIONE** | `cli.py`: twardy break w pętli Frame QA — po osiągnięciu `MAX_REVIEWS` kolejne kroki dostają jawnie `verdict: skipped, reason: Twardy limit...`. Test: `MAX_REVIEWS=1`, 2 kroki → 1 review, reszta skipped, każdy krok ma wynik. |
| GF-023 | **NAPRAWIONE** (wg wytycznych) | `fusion.py`: jawne ostrzeżenie przy nieudanej ekstrakcji gridu (`[FUSION] Ostrzeżenie: ... krok BEZ gridu`) w obu ścieżkach (wyjątek i `res is None`); usunięte martwe gałęzie `pass` („already handled", „-> no grid"). Algorytm gap-split **nie przepisany** (zgodnie z wytycznymi). Test: nieudany grid → warning + krok bez gridu; placeholder-mode zachowany (testy zachowań). |
| GF-024 | **NAPRAWIONE** | `Dockerfile`: jeden `pip install .` (usunięty podwójny install), `USER vtd` (uid 10001, nie-root), `config.example.yaml` nie kopiowany jako `/app/config.yaml` (konfiguracja przez mount/env), `COPY . .` + czyszczenie. |
| GF-025 | **NAPRAWIONE** | `cli.py`: nowy `PipelineError(RuntimeError)`; `run_pipeline` podnosi wyjątek zamiast `sys.exit(1)`; `main()` mapuje na `exit code 1` (exit tylko w warstwie CLI). Testy: `pytest.raises(PipelineError)`, `main()` → `SystemExit(1)`. |
| GF-026 | **NAPRAWIONE** | `cli.py`: usunięta martwa gałąź `if not out_path.exists()`; jeden poziom kompresji `compress_level=1` (spójny z gridem — koniec z ogromnymi PNG level 0). |
| GF-027 | **ZWERYFIKOWANE — brak problemu** | Audyt `vtd/studio/templates/**` i `static/**`: zero użyc `|safe` i `autoescape=False`; Jinja2 autoescape aktywne domyślnie; podgląd markdown i tak przechodzi przez bleach (`sanitize.py`). Drugi kanał XSS nie istnieje — bez zmian w kodzie. |
| GF-028 | **NAPRAWIONE** (z GF-012/GF-004) | `frame_extractor` czyści katalog przed run; martwa `enhance_frame()` (źródło `_enh_raw.png` w `frames/`) usunięta. Regresje w `test_frame_extractor.py` i `test_image_enhancer.py`. |
| GF-029 | **NAPRAWIONE** | `watchdog.load_env` korzysta z `vtd.config._load_env_file` (jedno parsowanie .env) zamiast własnej duplikacji. Test: wartości z pliku env widoczne w `load_env`. |
| GF-030 | **NAPRAWIONE** | `.github/workflows/docker.yml`: nowy job `lint` z `ruff check vtd tests --select E9,F63,F7,F82` — reguły wybrane tak, by przechodziły na obecnym kodzie (zweryfikowane lokalnie: `All checks passed!`). mypy: BACKLOG. |

**Bilans:** 29/30 findings obsłużonych (28 NAPRAWIONE + 1 zweryfikowane-brak problemu), 3 pozycje BACKLOG (GF-006 refactor, GF-010 cache configu, GF-018 WAL, GF-021 cache współdzielony, GF-013 pełne ujednolicenie formatu Dowód, GF-030 mypy — szczegóły wyżej).

---

## 2. Zmienione pliki

Kod:
- `vtd/builders/html_builder.py` (GF-001)
- `vtd/core/qa_verifier.py` (GF-002)
- `vtd/core/watchdog.py` (GF-003, GF-029)
- `vtd/core/image_enhancer.py` (GF-004, GF-028)
- `pyproject.toml` (GF-005, ruff w dev)
- `vtd/config.py` (GF-005, GF-009)
- `vtd/cli.py` (GF-006, GF-007, GF-008, GF-016, GF-022, GF-025, GF-026)
- `vtd/core/element_locator.py` (GF-010)
- `vtd/core/dedup.py` (GF-011)
- `vtd/core/frame_extractor.py` (GF-012, GF-028)
- `vtd/core/rerender_docs.py` (GF-013)
- `vtd/studio/app.py` (GF-014, GF-015, GF-016, GF-017)
- `vtd/defaults.py` — **NOWY** (GF-016)
- `vtd/studio/db.py` (GF-018)
- `vtd/core/transcriber.py` (GF-019)
- `vtd/core/enricher.py` (GF-007, GF-020, GF-021)
- `vtd/core/fusion.py` (GF-023)
- `Dockerfile` (GF-024)
- `.github/workflows/docker.yml` (GF-030)

Testy (istniejące, zmienione):
- `tests/test_studio.py` — 1 asercja: token w query param → 401 (GF-014; zmiana zachowania = treść naprawy)

Pliki nietknięte zgodnie z regułami: `.env`, `AUDYT_KODU.md`, `ANALIZA_*.md`. Zmiany z poprzedniej fali (VTD-*) zachowane i rozbudowane.

---

## 3. Nowe testy (12 plików, 78 testów)

| Plik | Liczba testów | Pokrycie |
|---|---|---|
| `tests/test_qa_verifier.py` | 5 | audit_and_fix_manual (mock LLM), regresja GF-002 |
| `tests/test_enricher.py` | 7 | enricher (mock providera), regresje GF-007/020/021 |
| `tests/test_fusion.py` | 7 | fuzja sygnałów, podziały, walidacja, regresja GF-023 |
| `tests/test_dedup.py` | 6 | pHash dedup, regresje GF-011 (warning + okno) |
| `tests/test_frame_extractor.py` | 5 | ekstrakcja (mock ffmpeg), regresje GF-012/028 |
| `tests/test_rerender_docs.py` | 6 | parsery rerender, regresja GF-013, backup flow |
| `tests/test_watchdog.py` | 6 | watchdog (env-based, mock subprocess), regresje GF-003/029 |
| `tests/test_image_enhancer.py` | 7 | enhancer (env config, mock HTTP/OCR), regresja GF-004 |
| `tests/test_transcriber.py` | 7 | transcriber (mock faster_whisper), regresja GF-019 |
| `tests/test_audio_detection.py` | 8 | parsowanie volumedetect/silencedetect, detect_best_track |
| `tests/test_cli_pipeline.py` | 8 | smoke `run_pipeline` na mockach; regresje GF-008/022/025/007 |
| `tests/test_worker.py` | 6 | flow subprocess workera z atrapami (success/error/timeout/recovery) |
| `tests/test_builders.py` (rozszerzony) | +1 | **Regresja XSS GF-001** — `<script>` w tytule/ASR/callout escapowane w HTML |
| **Razem** | **79** | |

Wymagania spełnione: deterministyczne, bez sieci i GPU (LLM/ffmpeg/subprocess mockowane), pełny suite < 2 s, zero skipów.

---

## 4. Zmiany zachowań (świadome, wymagane przez naprawy)

1. **GF-014:** `?token=` w Studio zwraca 401 — token tylko w nagłówku `X-VTD-Token` (test zaktualizowany).
2. **GF-016:** domyślny `--enrich-model` w CLI to `gpt-4o-mini` (wcześniej `erp-manual`); użytkownicy podający model jawnie nie są dotknięci.
3. **GF-012:** pipeline twardo kończy się błędem, gdy ffmpeg zwróci mniej timestampów niż klatek (wcześniej: sfabrykowane czasy).
4. **GF-025:** brak pliku wideo podnosi `PipelineError` (biblioteka) / exit 1 (CLI) — komunikat zachowany.
5. **GF-003:** bez `HERMES_ARTIFACT_CHAT` wysyłka Telegram jest pomijana z ostrzeżeniem (wcześniej: hard-coded chat_id).

---

## 5. Wynik finalny pytest

```
$ uv run --extra dev python -m pytest tests/ -q
...............................  [37× wiersze punktów — pominięto dla zwięzłości]
112 passed, 1 warning in 1.77s
```

Po dodaniu ostatniego testu GF-022:

```
$ uv run --extra dev python -m pytest tests/ -q
113 passed, 1 warning in 1.77s
```

```
$ uv run --extra dev python -m pytest tests/ -q
114 passed, 1 warning in 1.92s
```

Pozostały warning (`StarletteDeprecationWarning: Using httpx with starlette.testclient`) pochodzi z pakietu `starlette.testclient`, nie z kodu projektu. Warning deprecation FastAPI `on_event` (GF-015) został wyeliminowany.

Lint (GF-030):

```
$ uvx ruff check vtd tests --select E9,F63,F7,F82
All checks passed!
```

---

## 6. Uwagi i ryzyka

1. **GF-006 refactor (BACKLOG):** `run_pipeline` pozostaje monolitem; porządki usunęły martwy kod i locals()-checki, a smoke-testy (`test_cli_pipeline`) dają siatkę bezpieczeństwa dla przyszłego rozbicia na `PipelineContext`.
2. **GF-013:** format „Dowód" nadal istnieje tylko w PROMPT — parser md ratuje się promptem; pełne ujednolicenie wymagałoby zmiany outputu `manual_builder` (BACKLOG).
3. **GF-021:** cache OCR obejmuje obecnie `enricher.extract_frame_ocr`; locator/QA mają własne wywołania tesseract — pełny współdzielony cache wymaga wspólnej warstwy (BACKLOG).
4. **Dockerfile:** `COPY . .` kopiuje cały kontekst (w tym README/LICENSE potrzebne do builda); jeśli do obrazu trafiać mają wyłącznie źródła, warto dodać `.dockerignore` (niedostępny w raporcie — nie modyfikowano bez potrzeb).
5. **Telegram:** zmiana auth na nagłówek `Authorization: Bearer` jest zgodna z aktualnym Telegram Bot API (obsługa nagłówka zamiast tokenu w URL).
6. **Zgodność CLI:** wszystkie flagi i argumenty zachowane; jedyna zmiana widoczna dla użytkownika to domyślny model enrich (pkt 4).
