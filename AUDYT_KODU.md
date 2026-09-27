# AUDYT JAKOŚCI KODU — video-to-doc (vtd)

**Typ audytu:** wykrywczy (detekcyjny). Nie naprawiano kodu, nie zmieniano plików źródłowych, nie tworzono commitów.
**Data:** 2026-09-26
**Zakres:** `vtd/` (~7,3 tys. linii), `tests/`, `Dockerfile`, `docker-compose.yml`, `.github/workflows/docker.yml`, `pyproject.toml`, `config.example.*`, `.env.example`, `.dockerignore`.
**Uwaga:** plik `.env` nie był odczytywany ani kopiowany (sekrety). Poniższe odwołania do linii zweryfikowano przez odczyt kodu.

---

## 1. Podsumowanie wykonawcze

**Ocena ogólna: 5/10** — solidny, działający prototyp o dobrej architekturze warstwowej (core / builders / studio / providers) i sensownej filozofii „manual-first / DRAFT", ale z istotnymi lukami w obszarze bezpieczeństwa Studio, obsługi procesów potomnych i spójności konfiguracji. Kod nosi ślady szybkiego rozwoju (duplikacja, martwy kod, `if 'x' in locals()`, komentarze-objaśnienia) i nie jest jeszcze produkcyjnie hartowany.

**Najważniejsze wnioski (3–5):**

1. **Studio nie ma żadnej autoryzacji, a w Dockerze binduje `0.0.0.0`** — każdy w sieci może uruchamiać pipeline, czytać logi/output i ściągać ZIP. Ścieżka wideo jest przyjmowana z formularza bez ograniczenia do katalogu `recordings`, co daje traversal do dowolnego pliku na dysku serwera.
2. **Worker jest kruchy procesowo** — `subprocess.Popen` bez timeoutu i bez `kill()` w ścieżce błędu (zombie/zawieszenie), brak idempotencji i wznowienia po restarcie (status `running` zostaje na zawsze), etapy mapowane przez dopasowanie polskich stringów z logu.
3. **Konfiguracja jest niespójna i cicho zawodna** — `load_config` połyka wszystkie wyjątki, YAML nie jest zależnością produkcyjną (mimo że Docker kopiuje `config.example.yaml` jako `config.yaml`), a resolver klucza API może wysłać klucz OpenRouter/OmniRoute na domyślny endpoint OpenAI.
4. **Bug funkcjonalny w Studio** — odwołanie do niezdefiniowanej zmiennej `PIPELINE_ROOT` sprawia, że auto-detekcja toru w `/new` oraz `/api/track-detect` zawsze kończą się błędem; dodatkowo katalog output jest tworzony dwukrotnie (raz w app.py, raz w workerze), zostawiając puste sieroty.
5. **Testy pokrywają tylko cienką warstwę** (config, buildery, annotator, klient OpenAI, element_locator, frame_qa, podstawy Studio). Brak testów dla `worker`, `fusion`, `dedup`, `audio_*`, `frame_extractor`, `cli`; jeden test (`test_element_locator`) wykonuje realne wywołanie sieciowe.

**Status uruchomienia testów:** `pytest` **nie jest zainstalowany** w dostępnym środowisku (`python3` = 3.14.7 z `~/.hermes/tools`, brak modułu `pytest`, brak jakiegokolwiek venv w projekcie). Zgodnie z instrukcją nie instalowano pakietów. **Wynik: nie uruchomiono — 0 passed / 0 failed / nie zweryfikowano.** Bramka CI (`docker.yml:37-39`) uruchamia `pytest -v` dopiero po `pip install -e ".[dev]"`, więc lokalna weryfikacja wymagała instalacji zależności, czego nie wykonano.

---

## 2. Znaleziska

### KRYTYCZNE

| ID | Waga | Moduł | Plik:linia | Problem | Ryzyko | Rekomendacja |
|----|------|-------|-----------|---------|--------|--------------|
| VTD-001 | KRYTYCZNA | studio | `vtd/studio/app.py:112-113`, `vtd/studio/app.py:444-445` | Odwołanie do **niezdefiniowanej** nazwy `PIPELINE_ROOT`. W `new_task_form` (112) i `api_track_detect` (444) kod robi `if str(PIPELINE_ROOT) not in _sys.path`. Nazwa nigdy nie jest przypisana w module → `NameError`. W `/new` jest połykany przez `except Exception` (122-123) → auto-detekcja toru **nigdy nie działa**, użytkownik widzi tylko błąd. W `api_track_detect` wyjątek leci do `except Exception` → **HTTP 500 dla każdego wywołania**. | Auto-detekcja toru audio (kluczowa funkcja UX) jest w 100% niedziałająca; endpoint API zwraca 500. | Usunąć martwą logikę `PIPELINE_ROOT` (pakiet jest instalowany, `vtd` jest importowalne) albo zdefiniować stałą. Dodać test integracyjny `/api/track-detect`. |
| VTD-002 | KRYTYCZNA | studio | `vtd/studio/app.py:163-174`, `vtd/studio/app.py:429-439` | Brak walidacji, że `video_path` znajduje się w `RECORDINGS_DIR`. Kod sprawdza tylko `exists()/is_file()`; jeśli ścieżka jest absolutna i istnieje, jest przyjmowana. Komentarz (174) wprost przyznaje: „already validated exists", bez ograniczenia katalogu. | **Path traversal / dowolny odczyt pliku**: atakujący może podać `/etc/passwd`-podobną ścieżkę lub dowolny plik wideo z systemu, a pipeline zapisze jego klatki/transkrypcję do katalogu output i udostępni przez `/results` i `/download`. | Ograniczyć ścieżkę do `RECORDINGS_DIR.resolve()` przez `Path.resolve()` + `is_relative_to`, odrzucać `..`, symlinki i ścieżki absolutne spoza dozwolonego korzenia. |
| VTD-003 | KRYTYCZNA | studio | `vtd/studio/app.py` (całość, brak `Depends`/auth), `Dockerfile:45`, `docker-compose.yml:10` | Brak jakiejkolwiek autoryzacji (brak tokenu, sesji, Basic Auth, allowlisty IP). `Dockerfile` startuje z `--host 0.0.0.0`, a compose publikuje port `9870:9870` na wszystkie interfejsy. | Każdy w sieci lokalnej/VPN może uruchamiać kosztowne zadania (płatne LLM), czytać logi (`/tasks/{id}/log`), transkrypcje i pobierać ZIP z dokumentami. | Dodać co najmniej prosty token/API-key dla Studio, domyślnie bindować `127.0.0.1`, w Dockerze wymagać jawnego `VTD_AUTH_TOKEN`; rozważyć reverse-proxy z auth. |

### WYSOKIE

| ID | Waga | Moduł | Plik:linia | Problem | Ryzyko | Rekomendacja |
|----|------|-------|-----------|---------|--------|--------------|
| VTD-004 | WYSOKA | studio | `vtd/studio/worker.py:166-200` | `subprocess.Popen` uruchamiany bez `timeout`; odczyt `for line in proc.stdout` blokuje wątek bez limitu; `proc.wait()` (200) bez timeoutu. W razie zawieszenia ffmpeg/Whispera worker wisi na zawsze, blokując całą kolejkę (jednowątkowy pętla). Brak też `proc.kill()`/`terminate()` przy błędzie. | Trwałe zawieszenie workera = martwa kolejka zadań; brak zombie-cleanup; brak możliwości anulowania. | Dodać `timeout` do `Popen.wait`, mechanizm `kill` po przekroczeniu limitu, obsługę `KeyboardInterrupt`, watchdog na czas życia procesu. |
| VTD-005 | WYSOKA | studio | `vtd/studio/worker.py:93-104`, `vtd/studio/db.py:40` | Brak idempotencji i wznowienia: zadanie ustawiane na `running` na starcie; jeśli proces padnie (crash/OOM/restart kontenera), status **nigdy** nie wróci do `queued`/`error`. Brak rekonstrukcji kolejki przy starcie z zadań `running`/`queued`. | „Duch" zadań `running` na zawsze; po restarcie kontenera zadania w kolejce nie są wznawiane; UI kłamie o stanie. | Przy starcie workera skanować `running`/`queued` i re-enqueue lub oznaczać `error`; dodać `heartbeat`/`started_at` z limitem. |
| VTD-006 | WYSOKA | studio | `vtd/studio/app.py:203`, `vtd/studio/db.py:80-111`, `vtd/studio/worker.py:106` | `app.py` wywołuje `worker.build_output_dir(...)` i tworzy katalog, po czym przekazuje `output_dir=` do `create_task`, ale INSERT w `db.py` **nie zawiera kolumny `output_dir`** — parametr jest po cichu ignorowany. Worker wywołuje `build_output_dir` ponownie (106) z nowym timestampem → powstaje **drugi** katalog, a pierwszy zostaje pustym sierotą. | Śmieciowe katalogi w `output/`; brak zgodności między tym, co pokazuje UI/app, a tym, gdzie faktycznie trafiają artefakty. | Wyliczyć `output_dir` raz (w workerze) albo dodać `output_dir` do INSERT; usunąć podwójne tworzenie. |
| VTD-007 | WYSOKA | studio | `vtd/studio/app.py:290-302`, `vtd/studio/templates/results.html` (użycie `|safe`, do weryfikacji) | Treść `INSTRUKCJA.md`/`TRANSKRYPCJA.md` (pochodząca z ASR i LLM) konwertowana `markdown.markdown(...)` bez sanityzacji i wstrzykiwana do widoku. Jeśli szablon renderuje to jako `|safe`, możliwy **stored XSS** (LLM/OCR może wygenerować `<script>`). | XSS w przeglądarce operatora Studio. | Sanityzować HTML (np. `bleach`) lub renderować bez `|safe`; dodać `Content-Security-Policy`. Wymaga potwierdzenia w szablonie (do weryfikacji). |

### ŚREDNIE

| ID | Waga | Moduł | Plik:linia | Problem | Ryzyko | Rekomendacja |
|----|------|-------|-----------|---------|--------|--------------|
| VTD-008 | ŚREDNIA | config | `vtd/config.py:160-174`, `vtd/config.py:133-134` | `load_config` połyka **wszystkie** wyjątki przy parsowaniu JSON/YAML (`except Exception: pass`). Błąd składni w `config.yaml` → milczące użycie domyślnych wartości (np. inny model, inny base_url). | Trudne do zdiagnozowania „dlaczego działa inaczej"; możliwe użycie domyślnego endpointu OpenAI zamiast firmowego gatewaya. | Logować ostrzeżenie z nazwą pliku i linią; rozważyć fail-fast dla jawnego `config_path`. |
| VTD-009 | ŚREDNIA | config/DevOps | `pyproject.toml:27-38` (brak `pyyaml`), `pyproject.toml:44` (tylko dev), `Dockerfile:29` | `pyyaml` jest w `optional-dependencies.dev`, ale nie w zależnościach produkcyjnych. `Dockerfile:29` kopiuje `config.example.yaml` jako `/app/config.yaml`. W obrazie bez `pyyaml` `load_config` trafia na `ImportError` (171-172) i **cicho ignoruje YAML** → działa na defaults. | Konfiguracja z pliku YAML w Dockerze jest fikcją; użytkownik myśli, że ustawił parametry, a pipeline ich nie używa. | Dodać `pyyaml` do zależności głównych albo używać JSON w obrazie; dodać walidację obecności parsera. |
| VTD-010 | ŚREDNIA | config/security | `vtd/config.py:39-45`, `vtd/config.py:183-185` | `get_api_key` ma fallback na `OPENAI_API_KEY`, `OPENROUTER_API_KEY`, `OMNIROUTE_API_KEY`, a `load_config` wpisuje `OPENAI_API_KEY`/`VTD_API_KEY` do `cfg.llm.api_key`. Klucz może pochodzić z innego providera niż skonfigurowany `base_url`. | **Wyciek klucza** do niewłaściwego endpointu (np. klucz OpenRouter wysłany do `api.openai.com`), błędne rozliczenia, blokady konta. | Nie mieszać kluczy między providerami; walidować zgodność `provider` ↔ `api_key_env`; usunąć „popularne fallbacki" lub związać je z providerem. |
| VTD-011 | ŚREDNIA | core | `vtd/core/audio_extractor.py:54,71,87`, `vtd/core/frame_extractor.py:34`, `vtd/core/watchdog.py:61,85,96` | Wywołania `subprocess.run` **bez `timeout`**. Zawieszony `ffmpeg`/`ffprobe`/`curl` blokuje pipeline bezterminowo. | Zawieszenie całego zadania; brak możliwości przerwania z UI. | Dodać spójne `timeout` + obsługę `subprocess.TimeoutExpired` dla wszystkich wywołań ffmpeg/ffprobe/curl. |
| VTD-012 | ŚREDNIA | studio | `vtd/studio/worker.py:182-198` | Mapowanie etapów oparte na dopasowaniu **polskich stringów** z `stdout` („Krok 1/6", „Frame QA", „adnotacji AI"). Zmiana dowolnego `print` w `cli.py` cicho psuje postęp w UI. | Nierzetelny monitoring; brak błędu przy rozjechaniu formatu. | Emitować ustrukturyzowany postęp (JSON lines / kody etapów) i parsować je zamiast prozy. |
| VTD-013 | ŚREDNIA | studio/db | `vtd/studio/db.py:148-152` | `update_task` buduje SQL przez f-string z nazw kolumn (`f"{k} = ?"`). Obecnie klucze pochodzą z kodu, więc nie ma bezpośredniej injekcji, ale brak whitelisty kolumn = ryzyko przy przyszłym rozszerzeniu o dane z formularza. | Potencjalna SQL injection w przyszłości; trudność audytu. | Dodać `ALLOWED_COLUMNS` i walidować klucze; rozważyć stałe, jawnie nazwane funkcje aktualizacji. |
| VTD-014 | ŚREDNIA | studio/db | `vtd/studio/db.py:58-61`, `vtd/studio/db.py:64-71` | Brak `PRAGMA journal_mode=WAL`, brak `busy_timeout`; połączenie otwierane/zamykane per operacja pod globalnym `threading.Lock`. Przy równoległych odczytach z FastAPI i zapisach workera możliwe `database is locked`. | Sporadyczne błędy zapisu/odczytu stanu zadań. | Włączyć WAL + `busy_timeout`, rozważyć pulę połączeń; nie trzymać locka globalnie dla całej aplikacji. |
| VTD-015 | ŚREDNIA | core | `vtd/core/watchdog.py:15,17,72,93` | Hardkodowane ścieżki i dane osobowe: `RECORDINGS_DIR = /home/kozlo/erp-workspace/recordings` (15), `ENV_FILE = ~/.hermes/.env` (17), skrypt `~/.hermes/artifacts-server/...` (93), domyślny `chat_id="6989419513"` (72). | Moduł niedziałający poza jedną maszyną; wyciek identyfikatorów do repo; brak integracji z `load_config`. | Przenieść ścieżki do konfiguracji; usunąć domyślne `chat_id`; nie trzymać ścieżek osobistych w kodzie. |
| VTD-016 | ŚREDNIA | core | `vtd/core/watchdog.py:40-67`, `vtd/core/watchdog.py:11` | `is_file_ready` blokuje wątek `time.sleep(2.5)` (51) na każdy plik; import `run_pipeline` na poziomie modułu (11) tworzy zależność od `cli` (i całego łańcucha core), a watchdog nie jest wpisany w `[project.scripts]`. | Wolne skanowanie; watchdog praktycznie nieużywalny jako komponent pakietu. | Uczynić watchdog opcjonalnym komponentem z konfiguracją; unikać blokującego snu w pętli skanującej. |
| VTD-017 | ŚREDNIA | cli | `vtd/cli.py:691-697` | Przy `--frame-qa --enrich` kod **mutuje `s.speech_text`**, dopisując `[Frame QA: ...]`. Ta zmutowana treść trafia później do `TRANSKRYPCJA.md`, `metadata.json` i promptu, zanieczyszczając dane źródłowe. | Transkrypcja przestaje być wiernym zapisem ASR; trudna diagnostyka; duplikacja kontekstu QA. | Przekazywać kontekst QA osobnym parametrem/polem, nie modyfikować `speech_text`. |
| VTD-018 | ŚREDNIA | cli | `vtd/cli.py:118-1154` | `run_pipeline` to funkcja monolityczna ~1000 linii z ~20 flagami i logiką: audio, transkrypcja, klatki, QA, adnotacje, nano-banana, enrich, QA audit, budowa manifestu, kopiowanie plików. | Bardzo trudne testowanie, modyfikacja i code review; wysoka szansa regresji. | Rozbić na kroki/orchestrator + moduły etapów z jasnymi kontraktami. |

### NISKIE

| ID | Waga | Moduł | Plik:linia | Problem | Ryzyko | Rekomendacja |
|----|------|-------|-----------|---------|--------|--------------|
| VTD-019 | NISKA | core | `vtd/core/image_enhancer.py:234`, `vtd/core/image_enhancer.py:328` | Martwy kod: `_gemini_text_call` oraz `enhance_frame` nie są nigdzie wywoływane (potwierdzone grepem). | Zwiększa powierzchnię utrzymania i myli czytelnika. | Usunąć lub oznaczyć jako eksperymentalne. |
| VTD-020 | NISKA | core | `vtd/core/image_enhancer.py:25,29,33,512` | Hardkodowany `OMNIROUTE_BASE = http://127.0.0.1:20128/v1`, lista modeli `cheaperinference/...`, `LOCATOR_COST` i stawki kosztów w kodzie. | Vendor lock-in; brak możliwości konfiguracji; ceny mogą się zdezaktualizować. | Przenieść do `config.py` / zmiennych środowiskowych. |
| VTD-021 | NISKA | core | `vtd/core/frame_sharpness.py:10` | `import numpy` na poziomie modułu, a `numpy` **nie jest** zadeklarowany w `pyproject.toml`. Działa tylko jeśli `numpy` przyjdzie tranzytywnie (prawdopodobnie przez `imagehash`). **Do weryfikacji.** | Na czystej instalacji `import vtd.core.frame_sharpness` może rzucić `ModuleNotFoundError`; ścieżka ostrości cicho spada do fallbacku. | Dodać jawną zależność `numpy` w `pyproject.toml`. |
| VTD-022 | NISKA | całość | 110 wystąpień `except Exception`, z czego 39 to `except ...: pass` (grep) | Powszechne ciche połykanie błędów (np. `cli.py:1051,1066,1074,1079,1089,1093,1118,1131`, `image_enhancer.py:157,249,306`, `app.py:50,101,318,323`). | Błędy znikają bez śladu; trudna diagnostyka. | Logować wyjątki (min. `logger.debug/warning`) zamiast `pass`. |
| VTD-023 | NISKA | cli | `vtd/cli.py:152-169`, `vtd/cli.py:675-680`, `vtd/cli.py:835-847`, `vtd/cli.py:861` | Duplikacja i „obronne" `if 'x' in locals()`: dwa identyczne bloki tworzenia `PipelineConfig` (152-169); wielokrotne sprawdzanie `locals()` dla `annotations_map`, `nano_stats`, `skipped_qa_steps`. | Kod trudny do czytania; ryzyko rozjazdu gałęzi. | Zawsze inicjalizować zmienne z góry; scalić gałęzie konfiguracji. |
| VTD-024 | NISKA | DevOps | `Dockerfile:24-32`, `Dockerfile:44-45`, brak `USER` | Kontener działa jako **root**; `pip install .` wykonane dwa razy (25 i 32) — niepotrzebna warstwa; brak pinowania wersji zależności (wszystkie `>=`). | Zwiększona powierzchnia ataku i rozmiar obrazu; niezreprodukowalne buildy. | Dodać `USER appuser`, skonsolidować instalację, rozważyć pin/lock. |
| VTD-025 | NISKA | DevOps | `.github/workflows/docker.yml:20-39`, `docker.yml:54,61,70` | CI uruchamia tylko `pytest` (brak lintu/typechecku); jeden Python (3.11) mimo deklaracji 3.10–3.12; akcje przypięte do major tagów (`@v4`,`@v5`) bez SHA; brak skanowania bezpieczeństwa obrazu. | Regresje jakościowe/typy nie wykrywane; ryzyko supply-chain. | Dodać `ruff`/`mypy`, macierz Pythona, przypiąć akcje do SHA, dodać `trivy`/`grype`. |
| VTD-026 | NISKA | studio/security | `vtd/studio/app.py:374-386`, `vtd/studio/app.py:262-271` | Endpointy `/download` i `/log` przyjmują `task_id` i zwracają pliki/treść bez żadnej autoryzacji ani weryfikacji właściciela (powiązane z VTD-003). `zipfile` buduje archiwum w pamięci — przy dużych outputach możliwy wysoki peak RAM. | Nieautoryzowany dostęp do artefaktów; OOM przy dużych zbiorach klatek. | Powiązać z auth; rozważyć strumieniowy ZIP. |
| VTD-027 | NISKA | providers | `vtd/providers/openai_client.py:95-129`, `vtd/providers/openai_client.py:203-212` | Retry bez `Retry-After` i bez jittera (`time.sleep(1.5 ** attempt)`); `check_health` zakłada endpoint `/models` (nie każdy gateway go ma). | Ryzyko thundering herd przy 429; fałszywe „unhealthy". | Dodać jitter i honorować `Retry-After`; traktować 404 na `/models` jako „nieznane", nie „down". |
| VTD-028 | NISKA | core | `vtd/core/image_enhancer.py:167-169` | `_urlopen_with_retry(d0["url"], ...)` pobiera dowolny URL zwrócony przez gateway (potencjalny SSRF po stronie serwera Studio). | Niska (gateway jest zaufany), ale warto ograniczyć. | Walidować schemat/host URL odpowiedzi. |

---

## 3. Pozytywy

- **Czytelny podział warstw:** `core/` (przetwarzanie), `builders/` (renderery), `studio/` (web), `providers/` (LLM) — zależności biegną w jedną stronę, brak cykli importów w `vtd/` (poza `watchdog → cli`, patrz VTD-016). `providers/` jest poprawnie odseparowane interfejsem `BaseLLMProvider` i fabryką.
- **Bezpieczeństwo danych jako zasada projektowa:** „manual-first / DRAFT / DO WERYFIKACJI", wymuszanie statusu (nigdy `FINAL` — `cli.py:99-100`, `manual_builder.py:399-401`, `html_builder.py:21-23`), separacja `internal/` vs `client-ready/` i wykluczanie WAV/promptu/QA z paczki klienta (`cli.py:1028-1067`). To dojrzałe podejście do odpowiedzialności za treść.
- **Solidne, parametryzowane SQL** w `db.py` (poza nazwami kolumn w VTD-013) — brak konkatenacji wartości; `CHECK` constraints na `mode`/`status`.
- **Deterministyczna adnotacja oddzielona od AI:** `annotator.py` rysuje (PIL), a LLM tylko planuje (`element_locator.py`), z fallbackiem offline `_fallback()` (115-120). Ładny rozdział „model wskazuje / kod rysuje".
- **Walidacja wizji i wierności:** `image_enhancer.validate_layer_uniformity` i `verify_text_fidelity` (OCR-diff + kontrola proporcji) to dobrze przemyślane bramki chroniące przed halucynacją obrazu.
- **Odporność auto-detekcji audio:** `audio_detection.py` nie rzuca wyjątków, zwraca ustrukturyzowane `warning`, ma fallback `/usr/bin/ffmpeg` dla systemd i limit 120 s dla długich nagrań.
- **Spójna higiena repo:** `.dockerignore` i `.gitignore` wykluczają `.env`, `data/`, `output/`, media i cache; `.env.example` jest kompletny i sensownie udokumentowany.

---

## 4. Luki w testach (priorytetyzowane)

**P0 — krytyczne ścieżki bez pokrycia:**

1. **Worker / kolejka** (`vtd/studio/worker.py`) — brak jakichkolwiek testów: enqueue, `_worker_loop`, `_execute_task`, ścieżka błędu, zachowanie przy crashu, brak timeoutu (VTD-004/005). To najbardziej ryzykowny moduł.
2. **Bezpieczeństwo wejścia Studio** — brak testów walidacji `video_path` (traversal, ścieżka spoza `recordings`), walidacji `mode`/`scene_threshold`/`track`, endpointów `/download` i `/log` (VTD-002/003/026).
3. **Auto-detekcja toru** — brak testu `/api/track-detect` i gałęzi w `/new`; bug `PIPELINE_ROOT` (VTD-001) nie zostałby wykryty przez obecny zestaw.
4. **Fusion** (`vtd/core/fusion.py`) — brak testów podziałów narracyjnych, siatki `grid_frames`, walidacji timestampów, przypadków granicznych (`end == start`, brak transkryptu, jeden frame). To serce logiki domenowej.

**P1 — ważne:**

5. **`db.py`** — brak testów `update_task` z niedozwolonymi kolumnami, limitów `list_tasks`, równoległego dostępu, zachowania przy brakującym `output_dir`.
6. **`audio_extractor` / `frame_extractor`** — brak testów parsera `parse_silence_log` (linie niepełne, brak `silence_end`), `get_audio_stream_count`, zachowania przy błędzie ffmpeg/`returncode != 0`, brak audio w pliku.
7. **`dedup.py`** — brak testów progu Hamminga, `min_time_delta`, zachowania przy `ImportError` (obecnie cichy `return frames`).
8. **`cli.run_pipeline`** — brak testu end-to-end na atrapie ffmpeg/whispera; brak testu budowy manifestu i podziału internal/client-ready.
9. **`image_enhancer`** — brak testów `_parse_bbox`, `_clamp01`, `draw_annotation*`, `compose`, `enhance_frame_located` (fallback do composite), `verify_target` (łańcuch modeli, `BRAK_WIDZENIA`).

**P2 — jakość i determinizm:**

10. **`test_element_locator.py:6-21`** — `test_plan_annotations_fallback` **nie mockuje** providera; przy `OPENAI_API_KEY=test-mock-key-123` z `conftest.py` wykona realne żądanie HTTP do `api.openai.com` z retry/backoff (~kilka sekund). Test jest niedeterministyczny i sieciozależny — należy wstrzyknąć atrapę providera.
11. **Brak testu `watchdog`** — ale po usunięciu hardkodowanych ścieżek (VTD-015) i tak wymaga refaktoryzacji pod DI.
12. **Brak testów `rerender_docs`** — parsery `_parse_steps_from_prompt` i `_parse_steps_from_existing_markdown` są podatne na regresje formatu; brak fixture'ów.
13. **Brak testów negatywnych buildera** — np. brak `frame_path`, `enriched_data` z brakującym `step_number`, `metadata=None`, próba `FINAL`.
14. **Brak testów `frame_sources.resolve_frame`** — hierarchia enhanced/annotated/original i warunek `_accepted`.

---

## 5. Rekomendowany plan naprawczy (kolejność działań, bez implementacji)

**Faza 0 — natychmiastowe (bezpieczeństwo i poprawność krytyczna):**

1. VTD-001 — usunąć/zdefiniować `PIPELINE_ROOT`; przywrócić działanie auto-detekcji toru i endpointu `/api/track-detect`.
2. VTD-003 / VTD-002 — dodać auth do Studio i ograniczyć `video_path` do `RECORDINGS_DIR`; domyślny bind na `127.0.0.1`.
3. VTD-007 — potwierdzić i wyłączyć `|safe` / dodać sanityzację HTML w widoku wyników.
4. VTD-006 — wyeliminować podwójne tworzenie katalogu output i martwy parametr `output_dir`.

**Faza 1 — stabilność procesowa:**

5. VTD-004 — timeouty i `kill` w workerze; obsługa zawieszeń.
6. VTD-005 — odzyskiwanie zadań `running`/`queued` po restarcie; idempotencja.
7. VTD-011 — spójne timeouty dla wszystkich `subprocess.run` w `core/`.
8. VTD-012 — ustrukturyzowany postęp zamiast parsowania polskich stringów.

**Faza 2 — konfiguracja i spójność:**

9. VTD-008 / VTD-009 / VTD-010 — naprawa ładowania i logowania błędów konfiguracji, `pyyaml` jako zależność produkcyjna, rozdzielenie kluczy per provider.
10. VTD-013 / VTD-014 — whitelista kolumn w `update_task`, WAL + `busy_timeout` w SQLite.

**Faza 3 — higiena i DevOps:**

11. VTD-019 / VTD-020 / VTD-021 — usunięcie martwego kodu, parametryzacja modeli/endpointów OmniRoute, jawna zależność `numpy`.
12. VTD-024 / VTD-025 — non-root w Dockerze, konsolidacja warstw, dodanie lintu/typechecku do CI, pinowanie akcji i zależności.
13. VTD-015 / VTD-016 — usunięcie hardkodowanych ścieżek i danych osobowych z `watchdog.py`.

**Faza 4 — refaktor i testy:**

14. VTD-017 / VTD-018 — rozbicie `run_pipeline` na etapy; oddzielenie kontekstu Frame QA od `speech_text`.
15. Uzupełnienie testów P0/P1 z sekcji 4, w szczególności worker, bezpieczeństwo wejścia, fusion i db; naprawa niedeterministycznego `test_element_locator`.
16. VTD-022 / VTD-023 — zastąpienie cichych `pass` logowaniem; usunięcie duplikacji i `if 'x' in locals()`.

---

## 6. Zakres wykonanych czynności

**Przeczytane pliki (pełna treść):**

- Dokumentacja i konfiguracja repo: `README.md`, `pyproject.toml`, `Dockerfile`, `docker-compose.yml`, `.github/workflows/docker.yml`, `.env.example`, `.dockerignore`, `.gitignore`, `config.example.yaml`.
- Rdzeń: `vtd/cli.py` (1204 linie), `vtd/config.py`, `vtd/__main__.py`, `vtd/__init__.py`.
- `vtd/core/`: `frame_extractor.py`, `audio_extractor.py`, `audio_detection.py`, `transcriber.py`, `fusion.py`, `dedup.py`, `enricher.py`, `qa_verifier.py`, `watchdog.py`, `rerender_docs.py`, `frame_sources.py`, `frame_qa.py`, `frame_sharpness.py`, `element_locator.py`, `annotator.py`, `image_enhancer.py`.
- `vtd/builders/`: `manual_builder.py`, `html_builder.py`.
- `vtd/studio/`: `app.py`, `db.py`, `worker.py`, `settings_store.py`.
- `vtd/providers/`: `base.py`, `factory.py`, `openai_client.py`.
- `tests/`: `conftest.py`, `test_studio.py`, `test_config.py`, `test_openai_client.py`, `test_builders.py`, `test_frame_qa.py`, `test_annotator.py`, `test_element_locator.py`.

**Uruchomione komendy (read-only / inspekcyjne):**

- `find` — inwentaryzacja plików projektu.
- `git status --short` — brak zmian w drzewie roboczym (audyt nie modyfikował źródeł).
- `grep -rn "PIPELINE_ROOT" vtd/` — potwierdzenie niezdefiniowanej nazwy (VTD-001).
- `grep -rn "def create_task" vtd/` — potwierdzenie pominięcia `output_dir` w INSERT (VTD-006).
- `grep -rn "/home/kozlo\|/home/" vtd/` — potwierdzenie hardkodowanych ścieżek (VTD-015).
- `grep -rn "subprocess.run\|subprocess.Popen" vtd/` — inwentaryzacja wywołań (VTD-011, VTD-004).
- `grep -rn "except Exception" vtd/ --include=*.py | wc -l` → 110; oraz zliczenie `except ...: pass` → 39 (VTD-022).
- `grep -rn "yaml" vtd/ --include=*.py` — potwierdzenie warunkowego importu YAML (VTD-009).
- `grep -rn "_gemini_text_call\|enhance_frame(" vtd/ --include=*.py` — potwierdzenie martwego kodu (VTD-019).
- `grep -n "timeout\|kill\|terminate\|wait(" vtd/studio/worker.py` — potwierdzenie braku timeoutu (VTD-004).
- `wc -l vtd/cli.py vtd/studio/app.py vtd/core/image_enhancer.py` → 1204 / 459 / 775 (VTD-018).
- `python3 -m pytest tests/ -q` — **nie uruchomiono** (brak modułu `pytest` w dostępnym interpreterze 3.14.7; brak venv w projekcie). Nie instalowano pakietów zgodnie z regułami audytu.

**Czego nie weryfikowano (oznaczone „do weryfikacji"):**

- Renderowanie `instrukcja_html` w `templates/results.html` (obecność `|safe`) — VTD-007.
- Faktyczna dostępność `numpy` tranzytywnie przez `imagehash` na czystej instalacji — VTD-021.
- Brak testów runtime dla ścieżek wymagających ffmpeg/tesseract/GPU — nie uruchamiano pipeline.

**Czego nie wykonano (zgodnie z regułami):**

- Nie naprawiano i nie edytowano żadnego pliku źródłowego; jedyny utworzony plik to `AUDYT_KODU.md`.
- Nie tworzono commitów ani pushów.
- Nie odczytywano ani nie kopiowano zawartości `.env`.

---

## 7. Weryfikacja niezależna (Hermes, po audycie)

**Potwierdzone w kodzie (znaleziska trafne):**

- **VTD-001** — `PIPELINE_ROOT` używane w `app.py:112-113, 444-445`, brak jakiejkolwiek definicji w module. ✅ potwierdzone.
- **VTD-006** — INSERT w `db.py:82-87` nie zawiera kolumny `output_dir` mimo przekazywania parametru. ✅ potwierdzone.
- **VTD-021** — `frame_sharpness.py:10` importuje `numpy`; w `pyproject.toml` tylko `imagehash>=4.3.1` (numpy co najwyżej tranzytywnie). ✅ potwierdzone.
- **VTD-007** — podniesione z „do weryfikacji" na **POTWIERDZONE**: `templates/results.html:21,28,35` renderuje `{{ ..._html | safe }}` dla treści pochodzącej z ASR/LLM → realny stored XSS.

**Testy — uruchomione niezależnie przez `uv run --extra dev python -m pytest tests/ -q`:**

- Wynik: **21 passed / 1 failed** (pytest niedostępny w systemowym pythonie, ale `uv` rozwiązał zależności).
- Fail: `tests/test_config.py::test_load_config_from_json` — `load_config` zwraca domyślne `base_url` (`https://api.openai.com/v1`) zamiast wartości z pliku configu (`https://openrouter.ai/api/v1`). Powtarzalne także z wyczyszczonym środowiskiem (`env -u OPENAI_*`) → **realna regresja kodu**, prawdopodobnie po commicie `e76783d` (env-var overrides). To nowe znalezisko (kandydat na VTD-029, WYSOKIE) — plikowa konfiguracja `base_url` jest ignorowana, co grozi wysyłaniem promptów na zły endpoint.

**Pozostałe:** `git status --short` po audycie pokazuje wyłącznie nowy `AUDYT_KODU.md` — brak modyfikacji źródeł, zgodnie z regułą audytu wykrywawczego.

---

## 8. Re-weryfikacja (drugi przebieg, 2026-09-26)

**Metoda:** skrypt `verify_audit_v2.py` (15 programowych sprawdzeń na stanie kodu z commita `1834f8f` — repo niezmienione od audytu, git czysty poza `AUDYT_KODU.md` + `uv.lock`) + ręczne rozstrzygnięcie 2 trafień heurystyki + ponowne uruchomienie testów.

**Wynik: 15/15 sprawdzonych znalezisk POTWIERDZONE.**

Dwa fałszywe alarmy heurystyki (nie audytu) rozstrzygnięte ręcznie:

- **VTD-002** — `relative_to(` w app.py występuje tylko przy budowie ZIP/listingu wyników, NIE przy walidacji `video_path`. Kod (app.py:162-174) przyjmuje dowolną istniejącą ścieżkę absolutną; komentarz wprost przyznaje brak kontroli traversal. ✅
- **VTD-003** — Dockerfile:45 ma `--host 0.0.0.0` w formie JSON-array (`CMD ["studio", "--host", "0.0.0.0", ...]`), której nie łapie grep po spójnym stringu. Brak auth potwierdzony. ✅

Wynik detalu: `except Exception=110, z pass=39` — dokładnie zgodne z audytem. Czasy mtime plików źródłowych: 19–21 września — **żadna sesja naprawcza nie modyfikowała kodu**.

**Testy (ponownie):** `uv run --extra dev python -m pytest tests/ -q` → **21 passed / 1 failed** (ten sam, stabilny fail `test_load_config_from_json` — VTD-029 z sekcji 7 nadal aktualny).

**Wniosek:** audyt ocenia jako wiarygodny w całości (0 halucynacji w próbce 15/28 znalezisk); lista KRYTYCZNE (VTD-001/002/003) potwierdzona i gotowa do zlecenia napraw.
---

## 9. Implementacja napraw (2026-09-26)

Wszystkie poniższe zmiany wprowadzono realnymi edycjami plików (weryfikowalne przez `git diff`), w kolejności faz A → B → C → testy. Zakres edycji zgodny z misją: `vtd/**`, `tests/**`, `pyproject.toml`, `Dockerfile`, `docker-compose.yml`, `.env.example`.

### Faza A — krytyczne

**VTD-001 — martwa logika `PIPELINE_ROOT`** — usunięto bloki `sys.path` odwołujące się do niezdefiniowanej `PIPELINE_ROOT` w `new_task_form` (dawne 111-113) i `api_track_detect` (dawne 443-445). Import `vtd.core.audio_detection` działa bezpośrednio (pakiet jest instalowalny), więc auto-detekcja toru i `/api/track-detect` przestają kończyć się `NameError`/HTTP 500.

**VTD-002 — walidacja `video_path`** — dodano `resolve_video_path()` w `vtd/studio/app.py`: ścieżka jest rozwiązywana przez `Path.resolve()` i musi spełniać `is_relative_to(RECORDINGS_DIR.resolve())`; odrzucane są traversal `..`, symlinki wychodzące poza katalog nagrań oraz ścieżki absolutne spoza `recordings` (HTTP 400 z jawnym komunikatem). Funkcji używają `create_task_endpoint`, `api_track_detect` i formularz `/new` (wspólna reguła zamiast trzech różnych wariantów).

**VTD-003 — autoryzacja tokenem** — dodano middleware HTTP w `app.py`: gdy ustawione `VTD_AUTH_TOKEN`, wszystkie endpointy poza `/health` wymagają nagłówka `X-VTD-Token` albo parametru `?token=` (fallback); błędny/brak tokenu → 401. Gdy token nieustawiony: `vtd/cli.py` (komenda `studio`) wymusza bind `127.0.0.1` i loguje ostrzeżenie (także warning przy starcie aplikacji). `docker-compose.yml` wymaga `VTD_AUTH_TOKEN` (`${VTD_AUTH_TOKEN:?...}` — compose nie wystartuje bez zmiennej), `Dockerfile` ma komentarz o zależności `--host 0.0.0.0` od tokenu, `.env.example` zawiera `VTD_AUTH_TOKEN` z opisem. Bez users/sessions — zgodnie z wytyczną.

**VTD-006 — podwójne tworzenie katalogu output** — decyzja: `output_dir` wylicza i tworzy **wyłącznie worker** (`_execute_task`); `app.py` nie wywołuje już `build_output_dir` i nie tworzy katalogów. Dodatkowo kolumna `output_dir` trafiła do INSERT w `db.create_task` (wcześniej parametr był po cichu ignorowany), a worker szanuje już ustawiony `output_dir` z rekordu zamiast generować nowy timestamp. Przy okazji naprawiono wywołanie `create_task` z `app.py` (było wywołanie kwargsami do funkcji przyjmującej jeden dict → `TypeError` przy każdym `POST /tasks`) oraz usunięto placeholder `log_path` (`task_{0}.log`) — `log_path` ustawia worker.

### Faza B — wysokie

**VTD-004 — timeout procesu potomnego** — `worker._execute_task`: stdout czyta wątek-pompa, główna pętla czeka na `proc.wait(timeout=TASK_TIMEOUT_SECONDS)` (4 h). Po `subprocess.TimeoutExpired`: `_terminate_process()` → `terminate()`, po grace period 10 s → `kill()`; zadanie kończy się statusem `error` z powodem „Przekroczono limit czasu przetwarzania". Wątek-pompa też ma limit join, więc worker nie wisi na zablokowanym rurze.

**VTD-005 — recovery po restarcie** — decyzja: zadania `running` przy starcie workera oznaczane są `error` („Zadanie przerwane przez restart workera") — bez re-enqueue, bo stan połówkowych artefaktów w `output_dir` jest nieznany; zadania `queued` są **wznawiane** (ponownie trafiają do kolejki). Mechanizm: `recover_tasks_on_startup()` (idempotentny, flaga `_recovered`) wywoływany z `start_worker()`; nowy `db.list_tasks_by_status()` do skanowania stanu.

**VTD-007 — stored XSS w wynikach** — dodano `bleach` do zależności (`pyproject.toml`) i moduł `vtd/studio/sanitize.py` z `render_markdown_safe()` (markdown → `bleach.clean` z whitelistą tagów/atrybutów/protokołów). `task_results` buduje `instrukcja_html`/`transkrypcja_html`/`completeness_html` wyłącznie przez tę funkcję (jedno miejsce); fallback `<pre>` jest escape'owany przez `html.escape`. Szablon `results.html` zachowuje `|safe`, ale wstrzykiwany HTML jest już sanityzowany.

**VTD-029 — `load_config` gubi `base_url` z pliku (fail `test_load_config_from_json`)** — diagnoza potwierdzona: `_load_env_file` wstrzykiwał wartości z `.env` do `os.environ`, a warstwa env-overrides (commit `e76783d`) traktowała je jako realne zmienne środowiskowe i **nadpisywała** wartości z pliku konfiguracji (repozytoryjny `.env` ma `OPENAI_BASE_URL`/`OPENAI_MODEL`). Naprawa: proweniencja wartości ( `_dotenv_injected` + `_is_real_env`) i jawna precedencja w `load_config`: **realna zmienna env > plik konfiguracji > plik `.env` > wartości domyślne**. Wartość z pliku wygrywa z defaultem, realny env-var wygrywa z plikiem — zgodnie z wymaganiem.

### Faza C — stabilność procesów

**VTD-011 — timeouty `subprocess` w core** — `audio_extractor.py`: `ffprobe` 30 s; ekstrakcja audio i silencedetect: limit skalowany z długością materiału (`media_timeout()`: min. 120 s, mnożnik 2× czasu trwania, max. 2 h; przy braku możliwości odczytu długości — 2 h). `frame_extractor.py`: wycięcie pojedynczej klatki 60 s; ekstrakcja scen skalowana 3× czasu trwania (dekodowanie całego wideo). `watchdog.py`: `ffprobe` 30 s, `curl`/skrypt wysyłkowy 60 s. Wszędzie obsługa `subprocess.TimeoutExpired` — czytelny wyjątek `RuntimeError` w torze pipeline, jawnie logowany błąd (nie `pass`) w watchdogu.

**Decyzja alternatywna (odstępstwo od wytycznej „60-120 s"):** dla operacji na **całym** nośniku (ekstrakcja audio, silencedetect, ekstrakcja scen) stały 120 s spowodowałby fałszywe błędy przy długich nagraniach (>10-20 min, typowe dla meetingów) — ffmpeg musi zdekodować cały strumień. Zastosowano więc minimum 120 s zgodne z wytyczną i skalowanie z czasem trwania (do 2 h). Krótkie wywołania (`ffprobe`, wycięcie 1 klatki, `curl`) mają stałe limity 30-60 s zgodnie z zakresem 60-120 s (ffprobe krótszy).

### Testy

Nowe testy (`tests/test_studio.py`, `tests/test_config.py`):
- walidacja `video_path`: akceptacja pliku wewnątrz `recordings` (absolutna i po nazwie), odrzucenie traversal `../`, ścieżki absolutnej spoza katalogu (`/etc/passwd`), symlinka wychodzącego poza katalog, pliku nieistniejącego;
- auth: z tokenem — 401 bez nagłówka / z błędnym tokenem, 200 z `X-VTD-Token` i z `?token=`, `/health` bez tokenu; bez tokenu — brak wymogu;
- `load_config`: base_url/enricher_model/port z pliku (regresja VTD-029), env-var wygrywa z plikiem, plik wygrywa z `.env`, `.env` wygrywa z defaultem;
- bonus: `render_markdown_safe` (usuwa `<script>`/`onerror`), recovery workera (`running`→`error`, `queued` re-enqueue, brak duplikacji), `create_task` persistuje `output_dir`.

**Wynik (`uv run --extra dev python -m pytest tests/ -q`): 35 passed, 0 failed** (baseline: 21 passed / 1 failed — naprawiony `test_load_config_from_json` + 13 nowych testów).

### Pliki zmienione

`vtd/studio/app.py`, `vtd/studio/worker.py`, `vtd/studio/db.py`, `vtd/studio/sanitize.py` (nowy), `vtd/config.py`, `vtd/cli.py`, `vtd/core/audio_extractor.py`, `vtd/core/frame_extractor.py`, `vtd/core/watchdog.py`, `pyproject.toml`, `Dockerfile`, `docker-compose.yml`, `.env.example`, `tests/test_studio.py`, `tests/test_config.py`, `AUDYT_KODU.md`.

### Pozostałe uwagi

- Nie zmieniano modułów poza zakresem (VTD-008/009/010/012-028 pozostają otwarte).
- `.env` nie był czytany ani kopiowany (diagnostyka VTD-029 wykonana programowo, bez odczytu wartości sekretów).
- Brak commit/push — zgodnie z regułą.
