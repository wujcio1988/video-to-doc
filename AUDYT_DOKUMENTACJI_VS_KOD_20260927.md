# Audyt zgodności DOKUMENTACJA vs KOD — video-to-doc (vtd)

- **Data:** 2026-09-27
- **Tryb:** WYKRYWAWCZY (discovery-only; bez zmian w kodzie i dokumentacji, bez commitów, `.env` nietknięty)
- **Zakres:** `README.md`, `AUDYT_KODU.md` (sekcje 5 i 9), skill `video-to-doc/SKILL.md`
- **Weryfikator:** DeepSeek-V4.1-Flash (@opencode-go), opencode v2
- **Stan repo w tej sesji:** `git status --short` = **czysty**; ostatnie commity `11c2364 / 566b6ba / 1c3d739`
- **Suíte testów (realny przebieg tej sesji):** `uv run --extra dev python -m pytest tests/ -q` → **121 passed, 1 warning in 2.06s**

---

## 1. Podsumowanie

### 1.1 Bilans punktów

| Miara | Wartość |
|---|---|
| Łączna liczba zweryfikowanych punktów | **60** |
| Grupa A — fazy pipeline'u | 9 |
| Grupa B — Key Features | 7 |
| Grupa C — interfejsy (CLI/Studio/auth) | 3 |
| Grupa D — kontrakty i bezpieczeństwo | 3 |
| Grupa E — Docker / CI / instalacja | 5 |
| Grupa F — plan naprawczy VTD-001…029 | 29 |
| Grupa G — aktualność samej dokumentacji | 4 |

### 1.2 Rozkład statusów (60 punktów)

| Status | Liczba | % |
|---|---|---|
| `ZREALIZOWANE` | **34** | 56.7% |
| `CZĘŚCIOWO` | **13** | 21.7% |
| `BRAK` | **7** | 11.7% |
| `NIEAKTUALNA DOKUMENTACJA` | **6** | 10.0% |
| `NIE DOTYCZY` | **0** | 0.0% |

Rozkład w grupie F (VTD-001…029): **15 zrealizowanych, 7 częściowych, 7 otwartych (BRAK)**.

### 1.3 Najważniejsze wnioski (5–8)

1. **Rdzeń pipeline'u działa dokładnie tak, jak opisuje README.** A1–A4, A6–A8 oraz buildery A9 są w pełni zrealizowane: `ffmpeg silencedetect` → Whisper → `select=gt(scene,…)` → pHash (`imagehash.phash`) → fusion, OCR TSV (`tesseract -l pol+eng --psm 6 tsv`), annotator z czterema typami adnotacji, enricher LLM i trzy buildery (MD/HTML/DOCX) z poprawnymi nazwami plików.
2. **„Frame QA & Sharpness (Laplacian rank & review)” jest półprawdą.** Ranking po wariancji Laplacian jest realny (`frame_sharpness.py`), ale „review” NIE używa wizji — `frame_qa.py` wywołuje `provider.chat_completion` na tekście OCR, a nie `vision_completion`.
3. **„Universal LLM & Vision Support” — VISION nie jest używany w pipeline.** Metoda `vision_completion` istnieje w interfejsie providera (`base.py:25`, `openai_client.py:157`), ale jest wywoływana wyłącznie w teście (`tests/test_openai_client.py:71`). Zero wywołań w `vtd/`. Ulepszanie obrazu (nano-banana) omija provider i używa własnego, częściowo hardkodowanego HTTP.
4. **Claim „Local First” / „brak sieci poza localhost” jest nadinterpretowany.** Sam proces mediów (ffmpeg/Whisper/klatki) jest lokalny, ale treść (transkrypcja/OCR) jest wysyłana do zewnętrznych LLM domyślnie na `https://api.openai.com/v1`, a `image_enhancer.py:21` celuje w zewnętrzne Gemini. Szablon `base.html:22` wprost twierdzi „brak sieci poza localhost” — to nieprawda przy konfiguracji chmurowej.
5. **Kontrakt auth Studia zmienił się i dokumentacja (skill) jest nieaktualna.** Kod akceptuje `X-VTD-Token` **lub** cookie `vtd_token` (ustawiane przez `/login`), a `?token=` jest **jawnie odrzucany**. Skill nadal mówi „`X-VTD-Token` LUB `?token=`". `.env.example` opisuje już nowy kontrakt poprawnie.
6. **Plan naprawczy AUDYT jest wykonany w ~52%.** Faza 0 (VTD-001/002/003/006/007) i większość Fazy 1 są domknięte; **otwarte pozostają**: VTD-012 (nadal parsowanie polskich stringów z stdout), VTD-014 (brak WAL/busy_timeout), VTD-018 (nadal monolityczny `run_pipeline`, cli.py 1221 linii), VTD-021 (`numpy` poza deps), VTD-023 (`if … in locals()` w cli.py), VTD-027 (retry bez jittera), VTD-028 (SSRF w image_enhancer). HINTy dołączone do zadania potwierdziły się co do joty.
7. **Dokumentacja użytkownika (README/skill) jest w dużym stopniu nieaktualna lub niekompletna.** Skill: „tests/ 8 plików” (realnie 20), „35 passed” (realnie 121), „naprawy z 26.09 niezacommitowane” (realnie `git status` czysty). README: brak jakiejkolwiek wzmianki o auth/cookie Studia, badge „Python 3.10+” vs `python:3.11-slim`, oraz brak opisu ~12 realnych funkcji (watchdog, nano-banana, rerender, qa_verifier, `/settings`, split internal/client-ready i in.).
8. **Manifest „Manual-First” jest uczciwie egzekwowany.** Dokumenty są stemplowane DRAFT/DO WERYFIKACJI, a auto-wysyłka (Telegram, zarówno DOCX jak i artefakt HTML) jest bramkowana flagą `--allow-telegram` (domyślnie `False`). Non-destructive: oryginalne klatki w `frames/`, adnotacje w `frames_annotated/`, ulepszone w `frames_enhanced/`.

---

## 2. Tabele per grupa

### Grupa A — fazy pipeline'u (README „Architecture Pipeline")

| ID | Status | Dowód (plik:linia + cytat) | Uwagi |
|---|---|---|---|
| A1 | `ZREALIZOWANE` | `vtd/core/audio_extractor.py:115` `def detect_silence(...)`; `:123` `silencedetect=noise={noise_db}dB:d={min_duration}` | ffmpeg `silencedetect` → segmenty mowy. Zgodne z README. |
| A2 | `ZREALIZOWANE` | `vtd/core/transcriber.py:23` `class Transcriber`; `:34` `WhisperModel(model_size, device=device, compute_type=compute_type)`; `:37` `model.transcribe` | faster-whisper użyty realnie. |
| A3 | `ZREALIZOWANE` | `vtd/core/frame_extractor.py:42` `-vf select='eq(n\,0)+gt(scene,{threshold})',scale=...`; `vtd/core/dedup.py:37` `imagehash.phash(...)`; `:48` `if min_dist > hamming_threshold` | Scene detect → pHash dedup → keyframes. Zgodne. |
| A4 | `ZREALIZOWANE` | `vtd/core/fusion.py` `fuse_signals_into_steps_v2(frames, pauses, transcripts, video_duration, max_step_seconds, grid_interval, video_path, frames_dir, min_step_seconds)` | Wyrównuje mowę ze zmianami scen (i pauzami). Zgodne z opisem „aligns speech with visual changes". |
| A5 | `CZĘŚCIOWO` | `vtd/core/frame_sharpness.py:10` `import numpy as np` (wariancja Laplacian) — ranking REALNY; ale `vtd/core/frame_qa.py` `review_step_frame` buduje prompt tekstowy z `extract_frame_ocr(...)` i woła `self.provider.chat_completion(...)` (nie `vision_completion`) | „Laplacian rank" = TAK; „review" NIE używa wizji/modelu z obrazem — tylko OCR + tekst. README sugeruje review wizualny. |
| A6 | `ZREALIZOWANE` | `vtd/core/element_locator.py` `locate_words` uruchamia `tesseract … -l pol+eng --psm 6 tsv`, parsuje nagłówek `left/top/width/height/conf/text`, próg `conf>=60` | Współrzędne z TSV. Zgodne. |
| A7 | `ZREALIZOWANE` | `vtd/core/annotator.py:56` `arrow()`, `:135` `highlight_rect()`, `:164` `numbered_badge()`, `:214` `dim_background()`; `annotate_frame` kolejność dim→highlight→arrow→badge | **Wszystkie 4 typy istnieją** (strzałki, ramki, badge, przyciemnienie). Zgodne. |
| A8 | `ZREALIZOWANE` | `vtd/core/enricher.py` `enrich_steps_with_llm(...)` → `provider.chat_completion`; prompt manualny wymusza `callout_type warning\|tip\|quote` | LLM Step Enrichment. Zgodne. |
| A9 | `ZREALIZOWANE` | `vtd/builders/manual_builder.py:326` `render_docx_manual`, `render_markdown_manual`; `vtd/builders/html_builder.py:226-360` osadzony `<style>` + `<meta name="viewport">`; `cli.py:1013,1018` produkuje `INSTRUKCJA.md`, `INSTRUKCJA.html`, `INSTRUKCJA.docx` | Nazwy i realizacja zgodne. **Edge:** przy braku `python-docx` `manual_builder.py:344-345` `except ImportError: return output_docx` — cichy no-op bez tworzenia pliku (ale `python-docx` jest zależnością główną, więc normalnie nie występuje). |

### Grupa B — funkcje (README „Key Features")

| ID | Status | Dowód (plik:linia + cytat) | Uwagi |
|---|---|---|---|
| B1 | `CZĘŚCIOWO` | `vtd/core/fusion.py` sygnatura `fuse_signals_into_steps_v2(frames, pauses, transcripts, …)` — **brak parametru OCR/on-screen-text** | README mówi o 4 sygnałach (narracja, pauzy, zmiany scen, OCR). Fusion scala realnie **3**; OCR jest używany niżej (enricher / element_locator / frame_qa), nie w fusion. |
| B2 | `ZREALIZOWANE` | `vtd/core/element_locator.py` (TSV) + `vtd/core/annotator.py`; CLI zapisuje do `frames_annotated/`, oryginały w `frames/` | Adnotacje UI ze współrzędnych OCR, non-destructive. Zgodne. |
| B3 | `ZREALIZOWANE` | `vtd/core/frame_sharpness.py:10` `import numpy as np`; `extract_sharp_candidates` + `rank_sharp_frames` (wariancja Laplacian) | Ranking po wariancji Laplacian — zgodne. Drobiazg: `config.py:25` opis pola `frame_qa_model` mówi „multimodalny", a kod robi `chat_completion`. |
| B4 | `CZĘŚCIOWO` | `vtd/providers/base.py:25` `vision_completion`; `vtd/providers/openai_client.py:157` (base64 data-URI `image_url`). GREP: `vision_completion` występuje TYLKO w `tests/test_openai_client.py:71` — **zero wywołań w `vtd/`** | Provider OpenAI-compatible (openai/openrouter/ollama) — TAK. Realne wsparcie VISION w pipeline — NIE. Frame QA = OCR+tekst; nano-banana = własny hardkodowany HTTP. |
| B5 | `ZREALIZOWANE` | `cli.py:1013,1018` `render_markdown_manual`/`render_docx_manual`; `html_builder.render_html_manual`; pliki `INSTRUKCJA.md/.html/.docx` | 3 formaty, nazwy plików zgodne z README. |
| B6 | `ZREALIZOWANE` | enricher `callout_type warning\|tip\|quote`; `manual_builder.py:210-212` (MD), `html_builder.py:178-190` (HTML), `manual_builder.py:576-596` (DOCX) | Builder realnie renderuje tips/warnings. |
| B7 | `CZĘŚCIOWO` | `manual_builder.py` `_render_meeting_markdown`: `:259` `## Podstawy spotkania`, `:265` `## Uczestnicy`, `:300` `## Decyzje`, `:302` `## Ustalenia`, `:304-307` `## Action items` (Owner/Termin), `:308` `## Pytania otwarte`, `:310` `## Ryzyka` | **5 sekcji istnieje**, ale są to **statyczne placeholdery „DO POTWIERDZENIA"**. Schemat `enriched_data` niesie tylko `meta/intro/steps` — brak pól meeting, więc sekcje nie są wypełniane przez LLM. |

### Grupa C — interfejsy

| ID | Status | Dowód (plik:linia + cytat) | Uwagi |
|---|---|---|---|
| C1 | `ZREALIZOWANE` | `cli.py` build_parser: `run` ma `--output/-o`, `--title/-t`, `--mode`, `--track`, `--enrich`, `--frame-qa`, `--annotate` (dodatkowo `--nano-banana`, `--nano-mode`, `--output-mode`, `--document-status`); `studio --host/--port` (`cli.py:72-73`); `rerender` (`cli.py:75-76`) → `main()` `cli.py:1212-1214` `rerender_output_dir(Path(args.output_dir))` | Wszystkie wymagane flagi istnieją i są użyte. Komenda `rerender` **istnieje** (skill był aktualny co do jej istnienia). |
| C2 | `CZĘŚCIOWO` | (a) `templates/index.html` — tabela nagrań + tabela zadań status/stage; (b) `app.py:546` `@app.get('/api/track-detect')` + `detect_best_track` (timeout 30s), `/new` też probe (`app.py:226-239`); (c) `templates/task_detail.html:44-88` polling `/tasks/{id}/json` co 2s + `/tasks/{id}/log` (80 linii); (d) `templates/results.html` — podgląd **tylko MD** (`INSTRUKCJA.md`, `TRANSKRYPCJA.md`, `COMPLETENESS_REPORT.md` przez `render_markdown_safe`), HTML i DOCX wyłącznie jako pozycje listy (nazwa/rozmiar) + download | (a)–(c) zgodne. **(d) „side-by-side preview of Markdown, HTML, and Word" — nieprawda dla HTML i DOCX** (brak realnego podglądu). Drobiazg: `index.html:5` hardkoduje `~/erp-workspace/recordings/`. |
| C3 | `NIEAKTUALNA DOKUMENTACJA` | `vtd/studio/app.py:68-90` middleware: akceptuje `X-VTD-Token` **albo** cookie `vtd_token` (HttpOnly/SameSite=Strict, ustawiane w `POST /login`, `app.py:107-128`); `app.py:74-75` komentarz „token w query param pozostaje ODRZUCONY". `.env.example:22-26` opisuje dokładnie ten kontrakt | Skill twierdzi „`X-VTD-Token` LUB `?token=`" — **`?token=` jest jawnie odrzucany**. Kontrakt to header **lub cookie** (login), nie query param. |

### Grupa D — kontrakty i bezpieczeństwo (README „Security & Privacy")

| ID | Status | Dowód (plik:linia + cytat) | Uwagi |
|---|---|---|---|
| D1 | `CZĘŚCIOWO` | README: „All video processing, audio extraction, transcription, and frame extraction occur entirely on your local machine." → **prawda** (ffmpeg/Whisper lokalne). Konfrontacja: `enricher`/`frame_qa`/`element_locator`/`qa_verifier` wołają `provider.chat_completion`, domyślny `base_url` `https://api.openai.com/v1`; `image_enhancer.py:21` `API_BASE = 'https://generativelanguage.googleapis.com/v1beta/models'`; `image_enhancer.py:27` `OMNIROUTE_BASE = os.environ.get('VTD_OMNIROUTE_BASE','http://127.0.0.1:20128/v1')`. `templates/base.html:22` twierdzi „brak sieci poza localhost" | Claim zawężony do mediów jest uczciwy, ale UI **nadinterpretuje** („brak sieci poza localhost") — przy konfiguracji chmurowej treść (transkrypcja/OCR) opuszcza maszynę. |
| D2 | `ZREALIZOWANE` | Stemple DRAFT/DO WERYFIKACJI: `manual_builder.py:47,137,250,600`, `html_builder.py:351,356`. Auto-wysyłka: `watchdog.py` `scan_and_process_once(mode, allow_telegram=False)`; wysyłka Telegram **tylko** wewnątrz `if allow_telegram:`; CLI `--allow-telegram` domyślnie `False`; bramkowane **zarówno** DOCX (`send_telegram_document`) jak i artefakt HTML (`send_telegram_artifact`) | „Nigdy nie rozpowszechnia automatycznie" — TAK, bramka jawna. Watchdog to osobne, opt-in CLI; nie jest wołany przez Studio. |
| D3 | `ZREALIZOWANE` | `vtd/core/frame_sources.py:10-11` hierarchia `resolve_frame`: enhanced (wymaga `*_accepted`) → annotated → original; katalogi `frames/`, `frames_annotated/`, `frames_enhanced/`; CLI kopiuje (`shutil.copytree`) do `internal/` i `client-ready/` | Klatki źródłowe nietknięte, adnotacje w osobnych katalogach. Zgodne. |

### Grupa E — Docker / CI / instalacja

| ID | Status | Dowód (plik:linia + cytat) | Uwagi |
|---|---|---|---|
| E1 | `ZREALIZOWANE` | `docker-compose.yml`: `image: ghcr.io/wujcio1988/video-to-doc:latest`; `ports: "9870:9870"`; `volumes: ./recordings:/app/recordings`, `./output:/app/output`, `./data:/app/data`; `env_file: .env`; `VTD_AUTH_TOKEN=${VTD_AUTH_TOKEN:?...}` | Port 9870 i 3 wolumeny zgodne z README. Compose **nie wystartuje** bez tokenu (celowe). |
| E2 | `ZREALIZOWANE` | `.github/workflows/docker.yml`: `build-and-push` → `ghcr.io/${{ github.repository }}` (== `ghcr.io/wujcio1988/video-to-doc`), tag `latest` na gałęzi domyślnej + semver; job `test` (python 3.11, ffmpeg + tesseract pol+eng, `pip install -e ".[dev]"`, `pytest -v`); job `lint` (ruff) | Tag zgadza się z README i compose. |
| E3 | `ZREALIZOWANE` | `vtd/core/audio_extractor.py:13-17` i `frame_extractor.py:13-17` (ffmpeg/ffprobe); `element_locator.py`/`enricher.py` `tesseract … -l pol+eng` | Kod realnie wymaga ffmpeg + tesseract (pol+eng). Zgodne. |
| E4 | `ZREALIZOWANE` | `pyproject.toml` `[project.optional-dependencies].dev = [pytest>=7, httpx>=0.24, ruff>=0.4]`; README (sekcja instalacji) używa `pip install -e ".[dev]"` | Składnia pasuje do pyproject. Zgodne. |
| E5 | `NIEAKTUALNA DOKUMENTACJA` | README badge `Python-3.10%2B` (linia 5) i `pyproject.toml` `requires-python = ">=3.10"` vs `Dockerfile` `FROM python:3.11-slim-bookworm` | Rozbieżność badge (3.10+) vs baza obrazu (3.11). Nie jest to błąd funkcjonalny, ale dokumentacja nie odzwierciedla realnego środowiska kontenera. |

### Grupa F — kompletność planu naprawczego AUDYT (VTD-001…029)

#### F1 — Faza 0

| ID | Status | Dowód (plik:linia + cytat) | Uwagi |
|---|---|---|---|
| VTD-001 | `ZREALIZOWANE` | GREP `PIPELINE_ROOT` → brak trafień w źródłach (tylko `__pycache__`); `app.py:229,551` importuje `detect_best_track` bezpośrednio | Usunięto manipulację `sys.path`. Zamknięte. |
| VTD-002 | `ZREALIZOWANE` | `vtd/studio/app.py:131-159` `def resolve_video_path` — `Path.resolve()` + `is_relative_to(root)` + `HTTPException(400)` | Traversal zablokowany. Zamknięte. |
| VTD-003 | `ZREALIZOWANE` | middleware `app.py:68-90`; `cli.py` wymusza `127.0.0.1` bez tokenu; compose wymaga `VTD_AUTH_TOKEN` | Auth+bind. Zamknięte (kontrakt header/cookie — patrz C3). |
| VTD-006 | `ZREALIZOWANE` | `db.py:86-87` INSERT zawiera `output_dir`; `:111` `create_task` ustawia; `worker.py:155-160` tylko worker buduje `output_dir` | Brak wyścigu/duplikacji. Zamknięte. |
| VTD-007 | `ZREALIZOWANE` | `vtd/studio/sanitize.py:35` `render_markdown_safe` (markdown → `bleach.clean` z whitelistą); `app.py:396` używa tego dla `instrukcja`/`transkrypcja`/`completeness` | XSS `\|safe` załatany (treść sanityzowana przed `\|safe`). Zamknięte. |

#### F2 — Faza 1

| ID | Status | Dowód (plik:linia + cytat) | Uwagi |
|---|---|---|---|
| VTD-004 | `ZREALIZOWANE` | `worker.py:27` `TASK_TIMEOUT_SECONDS = 4*60*60`; `:258-263` `proc.wait(timeout=...)` + `_terminate_process`; `:96-112` terminate → grace → kill | Timeout + kill workera. Zamknięte. |
| VTD-005 | `ZREALIZOWANE` | `worker.py:72-93` `recover_tasks_on_startup` (running→error, queued re-enqueue), wołane z `start_worker` `:65` | Recovery kolejki. Zamknięte. |
| VTD-011 | `ZREALIZOWANE` | `audio_extractor.py:10` `FFPROBE_TIMEOUT=30`, `:30` `media_timeout`; `frame_extractor.py:11` `FFMPEG_FRAME_TIMEOUT=60`; watchdog ffprobe 30 / curl 60 | Timeouty subprocess. Zamknięte. |
| VTD-012 | `BRAK` | `worker.py:238-253` nadal dopasowuje polskie stringi z stdout: `'Krok 1/6'`, `'Krok 2/6'`, …, `'Frame QA'`, `'adnotacji AI'` | Ustrukturyzowany postęp **nie** został wprowadzony — nadal parsowanie stringów. **OTWARTE.** |

#### F3 — Faza 2

| ID | Status | Dowód (plik:linia + cytat) | Uwagi |
|---|---|---|---|
| VTD-008 | `CZĘŚCIOWO` | `config.py:197-200` jawny warning o braku `pyyaml`; `:202-203` `except Exception as e: print(f'OSTRZEŻENIE: nie można wczytać pliku konfiguracji {cand}: {e}')` | Błędy nie są już ciche, ale nadal następuje ciche przejście do domyślnych (brak fail-fast dla jawnego `config_path`). |
| VTD-009 | `ZREALIZOWANE` | `pyproject.toml:41` `pyyaml>=6.0.0` (komentarz `GF-005`) w zależnościach podstawowych | `pyyaml` nie jest już tylko w dev. Zamknięte. |
| VTD-010 | `ZREALIZOWANE` | `config.py:29` `get_api_key` wg `base_url`: `:44` openrouter→`OPENROUTER_API_KEY`; `:46` omniroute/localhost→`OMNIROUTE_API_KEY,OPENAI_API_KEY`; else `OPENAI_API_KEY` (`GF-009`) | Klucze nie przeciekają między providerami. Zamknięte. |
| VTD-013 | `ZREALIZOWANE` | `db.py:154-159` `_ALLOWED_TASK_COLUMNS` (frozenset); `:166-168` rzuca `ValueError` dla nieznanej kolumny (`GF-018`) | Whitelist `update_task`. Zamknięte. |
| VTD-014 | `BRAK` | GREP `journal_mode\|WAL\|busy_timeout` → **zero trafień** w `vtd/`; `db.py:58-61` zwykłe `sqlite3.connect(...)` bez PRAGMA | **Brak WAL i busy_timeout. OTWARTE.** |

#### F4 — Faza 3

| ID | Status | Dowód (plik:linia + cytat) | Uwagi |
|---|---|---|---|
| VTD-015 | `CZĘŚCIOWO` | `watchdog.py:18` `RECORDINGS_DIR = Path(os.environ.get('VTD_RECORDINGS_DIR','./recordings'))`; `:20` `ENV_FILE` konfigurowalny; `:88` chat_id już nie hardkodowany (`GF-003`). ALE `:122` `script = Path.home()/'.hermes'/'artifacts-server'/'scripts'/'hermes-send-artifact.sh'`; `:171-176` `device='cuda'`, `model_size='large-v3'`, `scene_threshold=0.05`, `enrich=True` | Ścieżki częściowo sparametryzowane; pozostał hardkod skryptu Telegram i parametrów pipeline. |
| VTD-016 | `CZĘŚCIOWO` | `watchdog.py:58` `time.sleep(2.5)` w `is_file_ready` (blokujące); `:11` `from vtd.cli import run_pipeline` na poziomie modułu; brak watchdoga w `[project.scripts]` (tylko `vtd`, `vtd-studio`) | Brak DI/entry pointu; sleep pozostał. |
| VTD-019 | `CZĘŚCIOWO` | `image_enhancer.py:334` komentarz `GF-004` — `enhance_frame` usunięty; ALE `_gemini_text_call` (`image_enhancer.py:240`) nie ma żadnego call-site (grep pokazuje wyłącznie definicję) | Martwy kod częściowo usunięty; `_gemini_text_call` nadal martwy. |
| VTD-020 | `CZĘŚCIOWO` | `image_enhancer.py:27` `OMNIROUTE_BASE = os.environ.get('VTD_OMNIROUTE_BASE','http://127.0.0.1:20128/v1')` (sparametryzowane); ALE `:22` `DEFAULT_MODEL` nano-banana-2, `:31` `VERIFY_MODELS`, `:35` `LAYER_MODELS`, `:456` `LOCATOR_MODEL`, `:457` `LOCATOR_COST` — hardkodowane | Parametryzacja OmniRoute częściowa; listy modeli/koszt nadal wpisane na sztywno. |
| VTD-021 | `BRAK` | `numpy` **nie występuje** w `pyproject.toml` (tylko tranzytywnie w `uv.lock`); `vtd/core/frame_sharpness.py:10` `import numpy as np` | **`numpy` poza deklarowanymi zależnościami. OTWARTE.** |
| VTD-024 | `ZREALIZOWANE` | `Dockerfile` — nie-root user `vtd` uid `10001` (`GF-024`); pojedyncze `pip install --no-cache-dir .` + `rm -f config.yaml`; `.dockerignore` wyklucza `.env` | Non-root Docker. Zamknięte. |
| VTD-025 | `CZĘŚCIOWO` | `.github/workflows/docker.yml` job `lint`: `ruff check vtd tests --select E9,F63,F7,F82` (`GF-030`). Brak typecheck, brak macierzy Pythonów (tylko 3.11), akcje pinowane do tagów major (`@v4`/`@v5`, bez SHA), brak skanu obrazu | Lint dodany; pozostałe elementy nie. |

#### F5 — Faza 4

| ID | Status | Dowód (plik:linia + cytat) | Uwagi |
|---|---|---|---|
| VTD-017 | `ZREALIZOWANE` | `cli.py:705` komentarz `GF-007 — QA idzie osobnym kanałem do promptu`; `frame_qa_results` jako osobna lista (`:381,451,688`); brak mutacji `speech_text` | Rozdzielenie kontekstu Frame QA vs `speech_text`. Zamknięte. |
| VTD-018 | `BRAK` | `run_pipeline` to nadal monolit `cli.py:127-1157` (plik 1221 linii) | **Refactor nie wykonany. OTWARTE.** |
| VTD-022 | `CZĘŚCIOWO` | GREP `except Exception:` w `vtd/` → **86 wystąpień**, wiele nadal z cichym `pass` (bez logowania) | Ciche przechwyty w większości niezaadresowane. |
| VTD-023 | `BRAK` | `cli.py` `in locals()` w liniach `862, 868, 879, 978`; `PipelineConfig` budowany dwukrotnie | **`locals()` nadal obecne; duplikacja nieusunięta. OTWARTE.** |
| VTD-026 | `ZREALIZOWANE` | globalny middleware auth obejmuje `/download` i `/log`; ZIP budowany na `tempfile` i streamowany (`app.py:470-504`, `GF-017`) zamiast w RAM | Zamknięte. |
| VTD-027 | `BRAK` | `openai_client.py:119,125` `time.sleep(1.5 ** attempt)` — brak jittera, brak obsługi `Retry-After` (ponawia tylko 429/5xx) | **OTWARTE.** |
| VTD-028 | `BRAK` | `image_enhancer.py:173-175` `elif d0.get('url'): with _urlopen_with_retry(d0['url'], timeout=60) as r: out_bytes = r.read()` | `urlopen` na URL zwróconym przez gateway = **ryzyko SSRF. OTWARTE.** |
| VTD-029 | `ZREALIZOWANE` | `config.py:132` `_is_real_env`, `:140` `_load_env_file`; precedencja: realna zmienna env > plik konfiguracji > `.env` > domyślne; `test_config` zielony (121 passed) | Zamknięte. |
| (testy P0/P1) | `ZREALIZOWANE` | `tests/` = 20 plików `test_*.py` (+ `conftest.py`); realny przebieg **121 passed** | Testy P0/P1 dodane/wykonane. |

**Weryfikacja HINTów dołączonych do zadania:**

| Hint | Werdykt |
|---|---|
| `numpy` podobno nadal poza deps | **PRAWDA** (VTD-021 otwarte) |
| WAL podobno otwarty | **PRAWDA** (VTD-014 otwarte) |
| refactor `run_pipeline` podobno w backlogu | **PRAWDA** (VTD-018 otwarte) |
| `locals()` podobno jeszcze w `cli.py` | **PRAWDA** (VTD-023 otwarte, linie 862/868/879/978) |
| watchdog podobno dalej hardkoduje device/model | **CZĘŚCIOWO PRAWDA** — ścieżki częściowo sparametryzowane, ale `:122` skrypt Telegram oraz `:171-176` `cuda`/`large-v3` hardkodowane |

### Grupa G — aktualność samej dokumentacji

| ID | Status | Dowód (plik:linia + cytat) | Uwagi |
|---|---|---|---|
| G1 | `NIEAKTUALNA DOKUMENTACJA` | SKILL.md: „tests/ — 8 plików" vs **realnie 20** `test_*.py` (`ls tests/test_*.py \| wc -l` = 20); baseline „35 passed" vs **realnie 121 passed**; „`?token=` w auth" vs kod **odrzuca** query param (`app.py:74-75`); „naprawy z 26.09 niezacommitowane" vs `git status --short` = **czysty** (commity `1c3d739`…); „VTD-015 ścieżki hardkodowane" vs stan częściowo naprawiony | Cały kluczowy „Kontekst audytu" w skillu jest przestarzały. |
| G2 | `NIEAKTUALNA DOKUMENTACJA` | README: badge `Python-3.10%2B` (`:5`) vs Dockerfile `3.11`; GREP `token\|auth\|cookie` w README → **0** (brak wzmianki o auth Studia); clone URL `github.com/wujcio1988/video-to-doc` (`:76,133`); GREP `watchdog\|nano\|rerender\|qa_verifier\|track-detect\|COMPLETENESS_REPORT\|client-ready` → **0** | README poprawny merytorycznie, ale niekompletny i częściowo nieaktualny (Python, brak auth, brak wzmianek o funkcjach). |
| G3 | `NIEAKTUALNA DOKUMENTACJA` | `config.example.yaml`/`.json` mają `scene_threshold: 0.05`; **`config.py:96` `scene_threshold: float = 0.35`** i `cli.py:48` help „domyślnie 0.35"; worker/db domyślnie `0.05`. `config.py:109` komentarz `output_mode` mówi `docx/html/both`, a CLI dopuszcza `internal\|client-ready\|both`. Nazwy pól Whisper (`whisper_model/whisper_language/whisper_device/whisper_compute_type`) — **zgodne** z `config.py:88-91` | Realny drift: `scene_threshold` (0.35 vs 0.05) oraz docstring `output_mode`. `.env.example` zawiera `GEMINI_API_KEY` (użyty jedynie legacy fallback w `image_enhancer`). |
| G4 | `NIEAKTUALNA DOKUMENTACJA` | Funkcje w kodzie **nieobecne w README** (grep = 0): `watchdog.py` (pętla Telegram), `image_enhancer`/`--nano-banana`, `rerender_docs` (`vtd rerender`), `qa_verifier.audit_and_fix_manual`, Studio `/settings` + `settings_store`, `/api/track-detect` + auto-detekcja toru, podział `internal/` vs `client-ready/` + `manifest.json`, `COMPLETENESS_REPORT.md`, `QA_AUDIT.md`, `PROMPT_DLA_AGENTA.txt`, hierarchia `frames_enhanced/`, dwukanałowa transkrypcja meeting (mic+client) | Lista udokumentowanych braków. Kod bogatszy niż dokumentacja. |

---

## 3. Braki w kodzie (dokumentacja obiecuje, kod nie realizuje)

| # | Obietnica dokumentacji | Realny stan kodu | Dotknięte ID |
|---|---|---|---|
| 1 | „Multi-Signal Fusion" scala **4** sygnały, w tym OCR/on-screen text | `fusion.fuse_signals_into_steps_v2` przyjmuje tylko `frames` (sceny), `pauses` (pauzy), `transcripts` (narracja) — brak parametru OCR | B1 |
| 2 | „Universal LLM & **Vision** Support" — przetwarzanie obrazu w pipeline | Metoda `vision_completion` istnieje w interfejsie, ale pipeline nigdzie jej nie woła (Frame QA używa OCR+tekstu; nano-banana używa własnego HTTP) | B4, A5 |
| 3 | „Frame QA … & **review**" implikujące ocenę wizualną klatki | „review" = prompt tekstowy z OCR i opisem kroku przez `chat_completion`; brak przekazania obrazu do modelu | A5 |
| 4 | Studio Results Viewer: „side-by-side preview of Markdown, HTML, and Word" | Realny podgląd **tylko MD**; HTML i DOCX to wyłącznie wpisy na liście plików z linkiem do pobrania | C2(d) |
| 5 | Tryb `meeting`: sekcje uczestnicy/decyzje/action items/terminy/pytania otwarte **wypełniane** | Sekcje renderowane, ale jako statyczne `DO POTWIERDZENIA`; schemat `enriched_data` nie niesie pól meeting | B7 |
| 6 | VTD-012: ustrukturyzowany postęp zamiast parsowania stringów | `worker.py:238-253` nadal dopasowuje `'Krok 1/6'`, `'Frame QA'`, `'adnotacji AI'` ze stdout | VTD-012 |
| 7 | VTD-014: WAL + busy_timeout dla SQLite | Brak jakiegokolwiek PRAGMA w `db.py` | VTD-014 |
| 8 | VTD-018: rozbicie monolitu `run_pipeline` | Monolit `cli.py:127-1157` bez zmian | VTD-018 |
| 9 | VTD-021: `numpy` jako jawna zależność | `numpy` nieobecne w `pyproject.toml`; importowane w `frame_sharpness.py` | VTD-021 |
| 10 | VTD-023: usunięcie `locals()` i duplikacji `PipelineConfig` | `in locals()` w `cli.py:862,868,879,978`; `PipelineConfig` budowany dwukrotnie | VTD-023 |
| 11 | VTD-027: retry z jitterem / obsługą `Retry-After` | `time.sleep(1.5 ** attempt)` bez jittera i bez `Retry-After` | VTD-027 |
| 12 | VTD-028: brak podatności SSRF w `image_enhancer` | `urlopen` na URL zwróconym przez gateway (`:173-175`) | VTD-028 |
| 13 | (A9) Eksport DOCX zawsze dostępny | Przy braku `python-docx` `manual_builder.py:344-345` cicho zwraca bez utworzenia pliku (brak komunikatu) | A9 (edge) |
| 14 | VTD-015/016: konfigurowalny watchdog | `watchdog.py:122` hardkoduje ścieżkę `hermes-send-artifact.sh`; `:171-176` hardkoduje `cuda`/`large-v3`/`enrich`; `:58` blokujący `sleep(2.5)` | VTD-015/016 |

---

## 4. Dokumentacja nieaktualna (kod się zmienił, tekst tego nie odzwierciedla)

| # | Dokument | Cytat / twierdzenie | Realny stan (dowód) |
|---|---|---|---|
| 1 | SKILL.md | „tests/ — 8 plików pytest" | 20 plików `test_*.py` (+`conftest.py`) — `ls tests/test_*.py` |
| 2 | SKILL.md | „baseline 2026-09-27: 35 passed" | Realnie **121 passed** (`pytest tests/ -q`) |
| 3 | SKILL.md | „Studio: … wymagają `X-VTD-Token` lub `?token=`" | `?token=` **odrzucane**; kontrakt to header **lub cookie** `vtd_token` z `/login` (`app.py:68-90,107-128`) |
| 4 | SKILL.md | „Naprawy z 26.09 są **niezacommitowane** (working tree)" | `git status --short` = czysty; zmiany w `1c3d739`/`566b6ba`/`11c2364` |
| 5 | SKILL.md | „`watchdog.py` ma ścieżki hardkodowane (VTD-015, otwarte)" | Częściowo już sparametryzowane env-em (`:18,:20,:88`); otwarte pozostały `:122` i `:171-176` |
| 6 | README | Badge `Python 3.10+` / „Python 3.10+" | Kontener: `FROM python:3.11-slim-bookworm` |
| 7 | README | Brak jakiejkolwiek informacji o auth Studia | Auth + cookie + `/login` + wymagany `VTD_AUTH_TOKEN` w compose są realne (`.env.example:22-26`) |
| 8 | README | „Multi-Signal Fusion" (sugestia 4 sygnałów z OCR) | Fusion scala 3 sygnały; OCR konsumowany niżej |
| 9 | README | Results Viewer: podgląd MD/HTML/DOCX side-by-side | Podgląd realny tylko dla MD |
| 10 | README | Tryb meeting: 5 sekcji jako treść | Sekcje istnieją, lecz są placeholderami |
| 11 | Skill/README | `config.example.yaml` sugeruje `scene_threshold: 0.05` | `config.py:96` default `0.35`; CLI help `0.35` — rozjazd domyślnych |
| 12 | `templates/index.html:5` | UI pokazuje `~/erp-workspace/recordings/` | `RECORDINGS_DIR` konfigurowalny (domyślnie `./recordings`) |
| 13 | `templates/new.html:91` | UI pokazuje output `~/erp-workspace/Customers/<klient>/…` | `worker.build_output_dir` używa `cfg.storage.output_dir/slug/mode_ts` (`worker.py:40-52`) |
| 14 | `templates/base.html:22` | „brak sieci poza localhost" | Fałszywe przy chmurowym LLM (domyślnie OpenAI, `image_enhancer` → Gemini) |
| 15 | `config.py:109` | docstring `output_mode` = `docx/html/both` | CLI dopuszcza `internal\|client-ready\|both` |

---

## 5. Rekomendacje (bez implementacji)

1. **Ujednolicić kontrakt auth w dokumentacji** — usunąć z SKILL.md wzmiankę o `?token=` i opisać realny tryb `X-VTD-Token` **lub** cookie `vtd_token` z `/login`; dopisać sekcję o auth do README (obecnie zero wzmianek).
2. **Zaktualizować baseline i liczby w SKILL.md** — „20 plików testowych" i „121 passed"; potwierdzić w `git log`, że naprawy z 26.09 są zacommitowane, i skorygować status VTD-015.
3. **Uczciwie oznaczyć zasięg „Local First"** — rozróżnić „media processing local" od „treść wysyłana do LLM"; poprawić komunikat `base.html:22`, który obiecuje brak sieci poza localhost.
4. **Zweryfikować i domknąć 7 otwartych VTD (012, 014, 018, 021, 023, 027, 028)** albo zdjąć je z planu jako świadomie odłożone (backlog) i odnotować to w AUDYT_KODU.md, aby plan odzwierciedlał rzeczywistość.
5. **Dodać `numpy` do zależności głównych** (`pyproject.toml`) — obecnie działa tylko tranzytywnie; grozi regresją przy zmianach drzewa zależności.
6. **Rozstrzygnąć „Vision"** — albo podłączyć `vision_completion` do realnego kroku (np. Frame QA / weryfikacja adnotacji), albo usunąć „Vision" z nazwy funkcji w README, by nie obiecywać niezrealizowanego.
7. **Dopisać brakujące funkcje do README** (watchdog, nano-banana, `vtd rerender`, qa_verifier, `/settings`, `internal/client-ready`, `COMPLETENESS_REPORT`) lub świadomie oznaczyć je jako eksperymentalne/wewnętrzne.
8. **Wyrównać domyślne `scene_threshold`** między `config.py` (0.35), CLI help a `config.example.*` (0.05) — jeden spójny default.
9. **Dodać fail-fast dla jawnego `config_path`** (VTD-008) i rozważyć WAL/busy_timeout (VTD-014) dla równoległego Studia.
10. **Uzupełnić podgląd w Results Viewer** o HTML (iframe z sandboxem) i ewentualnie konwersję DOCX→HTML, albo zmienić opis README na zgodny ze stanem faktycznym.

---

## 6. Sekcja prawdy

### 6.1 Pliki przeczytane w tej sesji (z zakresami)

- **Dokumentacja/konfiguracja:** `README.md` (linie 1–70, 103–243), `AUDYT_KODU.md` (sekcje 4/5/9 — VTD-001…029), `pyproject.toml`, `config.example.yaml`, `config.example.json`, `.env.example` (1–27), `Dockerfile` (1–44), `docker-compose.yml` (1–26), `.dockerignore`, `.github/workflows/docker.yml`, `SKILL.md` (video-to-doc).
- **CLI/core:** `vtd/cli.py` (build_parser, `run_pipeline` 127–1157, `main()` ~1212), `vtd/config.py` (1–263), `vtd/core/transcriber.py` (1–51), `audio_extractor.py` (1–130), `audio_detection.py` (1–356), `frame_extractor.py` (1–107), `dedup.py` (1–52), `fusion.py`, `frame_qa.py`, `frame_sharpness.py`, `element_locator.py`, `annotator.py`, `enricher.py`, `image_enhancer.py` (60–199, 240–339), `qa_verifier.py` (1–75), `rerender_docs.py` (1–313), `frame_sources.py` (1–31), `watchdog.py`.
- **Buildery/providery/studio:** `vtd/builders/manual_builder.py` (1–607), `vtd/builders/html_builder.py` (1–364), `vtd/providers/base.py`, `openai_client.py`, `factory.py`, `vtd/studio/app.py` (1–564), `db.py` (1–179), `worker.py` (1–292), `sanitize.py` (1–49), `settings_store.py` (1–71).
- **Szablony/UI:** `templates/index.html`, `new.html`, `task_detail.html`, `results.html`, `base.html`, `static/app.js`.
- **Testy (struktura):** `tests/` — listing i liczność.

### 6.2 Komendy uruchomione w tej sesji

- `git log --oneline -8` → `11c2364 …`, `566b6ba …`, `1c3d739 …`, `1834f8f …`, `e76783d …`, `8a2de6d …`, `514dc6d …`
- `git status --short` → **pusty (czysty)**
- `ls tests/test_*.py | wc -l` → **20**
- `grep -rn "PIPELINE_ROOT"` → brak w źródłach
- `grep -rn "vision_completion" vtd/ tests/` → tylko `providers` + `tests/test_openai_client.py:71`
- `grep -rn "journal_mode\|WAL\|busy_timeout" vtd/` → **0 trafień**
- `grep -rn "in locals()" vtd/` → `cli.py:862,868,879,978`
- `grep -rn "except Exception:" vtd/ --include=*.py | wc -l` → **86**
- `grep` o `token|auth|cookie` oraz `watchdog|nano|rerender|qa_verifier|COMPLETENESS_REPORT|client-ready` w README → **0 trafień**
- `uv run --extra dev python -m pytest tests/ -q` → **121 passed, 1 warning in 2.06s**

### 6.3 Czego NIE zrobiono / ograniczenia

- **Nie zmieniano** żadnego pliku kodu ani dokumentacji (poza utworzeniem tego raportu). **Nie commitowano.** Plik `.env` nietknięty.
- **Nie uruchamiano** realnego pipeline'u ani Whisper (brak nagrania; świadomie pominięto ze względu na czas/wymagania). Analiza pipeline'u opiera się na odczycie kodu i ścieżek wywołań.
- **Nie re-weryfikowano niezależnie każdego cytatu z sekcji 4 `AUDYT_KODU.md`** — korzystano z odczytów w tej sesji; cytaty z sekcji 4 AUDYT nie są tu powtarzane bez własnego odczytu.
- Dla kilku punktów „NIE DOTYCZY" nie wystąpiło — wszystkie 60 punktów miało zastosowanie i zostało ocenionych z dowodem.
- Werdykty `ZREALIZOWANE`/`CZĘŚCIOWO` oparte są na zachowaniu kodu i statycznych ścieżkach wywołań, nie na testach end-to-end z prawdziwym nagraniem.
- Raport powstał w trybie WYKRYWAWCZYM; rekomendacje (sekcja 5) są propozycjami, nie zostały zaimplementowane.
