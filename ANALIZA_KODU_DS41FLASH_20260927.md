# Analiza jakości kodu — Video-to-Doc (vtd)

- **Data:** 2026-09-27
- **Model/sesja:** DeepSeek-V4.1-Flash (provider opencode-go), OpenCode v2
- **Tryb:** WYKRYWAWCZY, niezależny od zera. Nie czytano istniejących raportów (`AUDYT_KODU.md`, `ANALIZA_*.md`, `NAPRAWY_*.md`) jako źródła.
- **Zakres:** aktualny working tree (łącznie ze zmianami niezacommitowanymi).
- **Testy:** `uv run --extra dev python -m pytest tests/ -q` → **114 passed, 1 warning in 2.07s** (StarletteDeprecationWarning: httpx/TestClient). Uruchomiono faktycznie.

---

## 1. Podsumowanie wykonawcze

**Ocena ogólna: 6.5 / 10**

Projekt jest dojrzały architektonicznie (czytelny podział core/builders/studio/providers, konsekwentne oznaczenia „GF-xxx"/„VTD-xxx" przy naprawach, spora liczba testów — 114 przechodzi), ale w aktualnym working tree pozostaje kilka realnych defektów funkcjonalnych i bezpieczeństwa, z których najpoważniejsze **nie są wykrywane przez testy**.

**Najważniejsze wnioski:**

1. **Funkcja `--annotate` jest całkowicie zepsuta i cicho maskowana.** `vtd/cli.py:506` i `:560` wołają `plan_annotations(..., llm_model="erp-code")`, a funkcja (`vtd/core/element_locator.py:110`) ma parametr `model=`. Każde wywołanie rzuca `TypeError`, który jest połykany przez `except Exception` w `vtd/cli.py:574-575`. Efekt: adnotacje AI nigdy nie działają, a pipeline kończy się „sukcesem" z pustą mapą adnotacji. [potwierdzone odczytem]
2. **Sekrety mogą trafić do obrazu Docker.** `.dockerignore` nie wyklucza `.env`, a `Dockerfile` robi `COPY . .`. Lokalny `.env` (z prawdziwymi kluczami) zostaje w warstwie build context. [potwierdzone odczytem]
3. **Integracja Telegram w `watchdog.py` jest nie do użycia.** URL to `https://api.telegram.org/bot/sendDocument` (brak `/bot<TOKEN>/`), a Telegram nie akceptuje nagłówka `Authorization: Bearer`. Jednocześnie test `test_watchdog.py` utrwala ten błędny model (sprawdza, że tokenu NIE ma w URL i JEST w nagłówku), więc regresja przechodzi „na zielono". [potwierdzone odczytem]
4. **Wychodzą na wierzch zależności od infrastruktury autora** — `image_enhancer.py` ma zaszyty gateway `cheaperinference/*`, model `nano-banana-2`, pliki `~/.hermes/.env`; `cli.py:717` zaszywa model QA `antigravity/gemini-3.8-flash-tiered`; `html_builder.py:356` wkleja do dokumentu klienta „NVIDIA RTX 5070". Utrudnia to instalację u trzeciej strony. [potwierdzone odczytem]
5. **Zarządzanie procesami/procesami potomnymi jest kruche.** `worker.py` startuje subprocess bez `start_new_session`/grupy procesów, więc po timeoucie `terminate/kill` nie zabija potomków ffmpeg; temp ZIP w `app.py` nie ma `try/finally` (wyciek przy rozłączeniu klienta). [potwierdzone odczytem]
6. **Konfiguracja i dokumentacja się rozjeżdżają** — `.env.example` reklamuje `?token=` (usunięte w GF-014), `config.example.yaml` nie zawiera kilku pól `PipelineConfig`, README mówi Python 3.10+ przy Dockerfile 3.11. [potwierdzone odczytem]

---

## 2. Znaleziska

Legenda wagi: KRYTYCZNA / WYSOKA / ŚREDNIA / NISKA. Pewność: [potwierdzone odczytem] / [do weryfikacji].

### KRYTYCZNE

#### DS-001 — `--annotate` zawsze rzuca TypeError i jest maskowane (adnotacje AI nie działają)
- **Waga:** KRYTYCZNA
- **Plik:** `vtd/cli.py:506`, `vtd/cli.py:560`, `vtd/core/element_locator.py:110`, maskowanie `vtd/cli.py:574-575`
- **Cytat:**
  - `vtd/cli.py:506` → `planned = plan_annotations(s.step_number, title_for_llm, desc_for_llm, located, llm_model="erp-code")`
  - `vtd/cli.py:560` → `g_planned = plan_annotations(s.step_number, title_for_llm, desc_for_llm, g_located, llm_model="erp-code")`
  - `vtd/core/element_locator.py:110` → `def plan_annotations(step_number: int, step_title: str, step_description: str, located: Dict[str, List[int]], model: Optional[str] = None:`
  - `vtd/cli.py:575` → `print(f"  [ANNOTATE] Błąd globalny adnotacji: {e}")`
- **Opis:** Nazwa argumentu w wywołaniu (`llm_model`) nie odpowiada sygnaturze (`model`). Python podnosi `TypeError: plan_annotations() got an unexpected keyword argument 'llm_model'` przy pierwszym kroku; wyjątek jest przechwytywany przez otaczający `try/except Exception` w pętli adnotacji i tylko logowany. `annotations_map` pozostaje puste dla wszystkich kroków, także dla klatek grid (drugie wywołanie).
- **Ryzyko:** Cała funkcja adnotacji wizualnych jest niedostępna, mimo że CLI, README i metadata raportują ją jako włączoną. Użytkownik dostaje dokument bez adnotacji i fałszywe „sukces". Defekt nie jest wykryty przez testy (brak testu ścieżki `--annotate`).
- **Rekomendacja:** Ujednolicić nazwę parametru (albo wywołanie, albo sygnaturę), a w warstwie adnotacji zawęzić `except` i logować błąd jako realny failure (nie-`pass`), tak by cicha degradacja była niemożliwa.
- **Pewność:** [potwierdzone odczytem]

### WYSOKIE

#### DS-002 — Telegram: URL bez tokenu bota + token w nagłówku (wysyłka nigdy się nie powiedzie)
- **Waga:** WYSOKA
- **Plik:** `vtd/core/watchdog.py:92-98`
- **Cytat:** `"https://api.telegram.org/bot/sendDocument"`, poprzedzone `"-H", f"Authorization: Bearer {bot_token}"`
- **Opis:** Telegram Bot API wymaga ścieżki `/bot<TOKEN>/sendDocument`; token w nagłówku `Authorization` nie jest obsługiwany. Wywołanie zawsze zwróci 404/401. Dodatkowo token trafia do `argv` procesu `curl` (widoczny w `ps`), mimo komentarza sugerującego bezpieczeństwo.
- **Ryzyko:** Automatyczna publikacja dokumentów po nagraniu nie działa; możliwe wycieki tokenu przez listę procesów.
- **Rekomendacja:** Zbudować poprawny URL z tokenem w ścieżce (lub użyć biblioteki HTTP z ciałem multipart), przekazywać token poza `argv` i zaktualizować test regresyjny tak, by odzwierciedlał realny kontrakt API.
- **Pewność:** [potwierdzone odczytem]

#### DS-003 — Zaszyty model QA nadpisuje konfigurację
- **Waga:** WYSOKA
- **Plik:** `vtd/cli.py:717`
- **Cytat:** `model="antigravity/gemini-3.8-flash-tiered"`
- **Opis:** Wywołanie `audit_and_fix_manual(...)` przekazuje zaszyty model, ignorując `cfg.llm.qa_verifier_model`, który użytkownik może ustawić w configu. Identyfikator `antigravity/...` to wewnętrzna nazwa gatewaya, nieprzenośna.
- **Ryzyko:** Użytkownik nie może skonfigurować modelu audytu; na cudzej infrastrukturze wywołanie może się nie powieść lub trafić do nieistniejącego modelu.
- **Rekomendacja:** Usunąć zaszyty `model=` i pozwolić `audit_and_fix_manual` użyć wartości domyślnej z konfiguracji (lub przekazać ją jawnie z `cfg`).
- **Pewność:** [potwierdzone odczytem]

#### DS-004 — `.env` nie jest wykluczony z kontekstu Dockera, a Dockerfile kopiuje cały katalog
- **Waga:** WYSOKA (bezpieczeństwo)
- **Plik:** `.dockerignore` (brak wpisu `.env`), `Dockerfile` (`COPY . .`)
- **Cytat:** `.dockerignore` zawiera m.in. `.git/`, `.pytest_cache/`, `data/`, `output/`, `recordings/`, `tests/`, `*.log`, `*.mkv`, `*.mp4`, `*.bak*` — brak `.env`; `Dockerfile` → `COPY . .`
- **Opis:** `.gitignore` ignoruje `.env`, ale `.dockerignore` już nie. `COPY . .` wciąga lokalny `.env` z kluczami API do warstwy obrazu (i możliwego pushu do rejestru). Linia `rm -f /app/config.yaml` usuwa tylko config, nie `.env`.
- **Ryzyko:** Wyciek sekretów (klucze OpenAI/OpenRouter/Gemini, `VTD_AUTH_TOKEN`) do obrazu i rejestru.
- **Rekomendacja:** Dodać `.env` i `*.env` do `.dockerignore`; rozważyć `.env*` oraz jawne whitelistowanie plików kopiowanych do obrazu.
- **Pewność:** [potwierdzone odczytem]

#### DS-005 — `image_enhancer.py` zaszyty na infrastrukturę autora (gateway, modele, klucze)
- **Waga:** WYSOKA
- **Plik:** `vtd/core/image_enhancer.py:21-50, 240-242, 265-331`
- **Cytat:** `DEFAULT_MODEL = "cheaperinference/nano-banana-2"`, `OMNIROUTE_BASE = os.environ.get("VTD_OMNIROUTE_BASE", "http://127.0.0.1:20128/v1")`, `_KEY_FILES = [... ~/.hermes/.env ...]`, `VERIFY_MODELS = ["cheaperinference/gemini-3.7-flash", "openrouter/google/gemini-3.8-flash"]`
- **Opis:** Domyślne modele i lokalne ścieżki kluczy wskazują na prywatny gateway autora. Domyślnie funkcja Nano Banana nie zadziała u innego użytkownika bez ręcznej konfiguracji env. Część jest konfigurowalna (GF-004), ale wartości domyślne pozostają „author-specific".
- **Ryzyko:** First-run failure; mylące komunikaty; dokument włączający `--nano-banana` nie działa „z pudełka".
- **Rekomendacja:** Uczynić gateway/modele w pełni konfigurowalnymi z `config.yaml`/env, usunąć odwołania do `~/.hermes`, dodać walidację „brak skonfigurowanego gatewaya" z czytelnym błędem.
- **Pewność:** [potwierdzone odczytem]

#### DS-006 — Worker nie tworzy nowej sesji procesu → osierocone ffmpeg po timeoucie
- **Waga:** WYSOKA
- **Plik:** `vtd/studio/worker.py:219`, `:96`, `:260-264`
- **Cytat:** `proc = subprocess.Popen(` z kwargs `stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1`; `_terminate_process` → `terminate`/`wait(grace)`/`kill`
- **Opis:** Brak `start_new_session=True` / `os.setsid`. Po przekroczeniu `TASK_TIMEOUT_SECONDS` ubijany jest tylko bezpośredni proces Pythona (`vtd run`); uruchomione przez niego dzieci ffmpeg/ffprobe mogą dalej działać.
- **Ryzyko:** Zombie/osierocone procesy ffmpeg zużywają CPU/GPU i blokują pliki; kolejne zadania mogą się kumulować.
- **Rekomendacja:** Uruchamiać subprocess w nowej grupie procesów i przy timeoucie zabijać całą grupę (`killpg`/`os.kill(-pid)`); rozważyć `prctl(PDEATHSIG)`.
- **Pewność:** [potwierdzone odczytem]

#### DS-007 — Frame QA nie używa wizji, mimo nazwy; parametr `timeout` martwy
- **Waga:** WYSOKA
- **Plik:** `vtd/core/frame_qa.py` (`FrameReviewer.__init__`, `review_step_frame`)
- **Cytat (odczyt):** recenzja buduje prompt z `extract_frame_ocr(...)` i woła `provider.chat_completion(...)`; klasa przechowuje `self.timeout` (domyślnie 120), ale nie używa go w żadnym wywołaniu.
- **Opis:** „FrameReviewer" nie wysyła obrazu do modelu — decyduje wyłącznie na podstawie tekstu OCR. Parametr `timeout` jest zadeklarowany i nigdy nie przekazany.
- **Ryzyko:** Jakość QA klatek jest niższa niż sugeruje nazwa; użytkownik nie może sterować timeoutem; rozbieżność nazwa/działanie myli.
- **Rekomendacja:** Albo użyć realnej wizji (`vision_completion`) i przekazać `timeout`, albo przemianować i usunąć martwy parametr, oraz dodać test, że obraz jest faktycznie wysyłany (jeśli to ma być QA wizualny).
- **Pewność:** [potwierdzone odczytem]

#### DS-009 — Studio w CLI po cichu nadpisuje `--host` na 127.0.0.1, gdy brak tokenu
- **Waga:** WYSOKA
- **Plik:** `vtd/cli.py` gałąź `studio` w `main()`
- **Cytat (odczyt):** `host = args.host or cfg.server.host`; następnie przy braku `VTD_AUTH_TOKEN` logowane ostrzeżenie i `host = "127.0.0.1"`.
- **Opis:** Nawet jawnie podane `--host 0.0.0.0` (a taki jest `CMD` w Dockerfile) zostaje nadpisane na loopback, gdy token nie jest ustawiony. Kontener staje się nieosiągalny z zewnątrz, a użytkownik widzi tylko wpis logu.
- **Ryzyko:** Trudna diagnostyka „Docker działa, ale strona nie odpowiada"; ciche łamanie oczekiwań CLI.
- **Rekomendacja:** Nie nadpisywać jawnego `--host`; albo odmówić startu z błędem i instrukcją, albo wymusić token jawnie. Udokumentować to zachowanie.
- **Pewność:** [potwierdzone odczytem]

### ŚREDNIE

#### DS-008 — Nadmiarowe, ciche `except Exception` maskują realne błędy pipeline
- **Waga:** ŚREDNIA
- **Plik:** `vtd/cli.py` (m.in. `:574`, `:644`, `:756`, `:1052`, `:1066`, `:1115`)
- **Cytat:** `vtd/cli.py:575` → `print(f"  [ANNOTATE] Błąd globalny adnotacji: {e}")`
- **Opis:** Wiele miejsc połyka wyjątki bez eskalacji (kopiowanie, manifest, frame QA, adnotacje). To właśnie ten wzorzec ukrył DS-001.
- **Ryzyko:** Cicha degradacja wyników; pipeline „zielony", artefakty niepełne.
- **Rekomendacja:** Zawężać wyjątki, zliczać degradacje do metadata/completeness i oznaczać je jako warning/failure zamiast tylko `print`.
- **Pewność:** [potwierdzone odczytem]

#### DS-010 — Auth w Studiu: brak CSRF i nieautoryzowalne zasoby statyczne/formularze
- **Waga:** ŚREDNIA
- **Plik:** `vtd/studio/app.py` (middleware `require_auth_token`, `AUTH_EXEMPT_PATHS`), szablony w `vtd/studio/templates`, `vtd/studio/static`
- **Cytat (odczyt):** middleware przepuszcza tylko ścieżki z `AUTH_EXEMPT_PATHS` (tylko `/health`); wymaga nagłówka `X-VTD-Token` porównywanego przez `hmac.compare_digest`.
- **Opis:** Przeglądarka nie dołącza własnego nagłówka do żądań `<img>/<link>` ani do natywnych submitów formularza HTML, więc przy włączonym tokenie zasoby statyczne i formularze dostają 401 (chyba że JS opakuje wszystko). Brak tokenu CSRF na endpointach POST.
- **Ryzyko:** Utrudniona/uszkodzona funkcjonalność przy włączonym tokenie; brak ochrony CSRF.
- **Rekomendacja:** Rozważyć cookie-session z CSRF dla UI + Bearer dla API, albo wykluczyć `/static` i jawnie obsłużyć autoryzację formularzy; „404/redirect" zamiast 401 dla zasobów statycznych.
- **Pewność:** [potwierdzone odczytem]

#### DS-011 — Nano Banana liczy `target_description` przed wzbogaceniem → gałąź zawsze pusta
- **Waga:** ŚREDNIA
- **Plik:** `vtd/cli.py:589-611` vs `vtd/cli.py:~700-710`
- **Cytat (odczyt):** `enriched_data = None` (589), `if nano_banana and mode == "manual":` (590), `if enriched_data and "steps" in enriched_data:` (611); `enrich_steps_with_llm(...)` wołane dopiero później.
- **Opis:** Blok Nano Banana wykonuje się przed etapem enrich, więc `enriched_data` jest zawsze `None`; `target_description` zawsze spada do `speech_text`. Opis „z kontekstu kroku" nigdy nie korzysta z danych LLM.
- **Ryzyko:** Jakość oznaczeń niższa niż zakładana; martwy fragment kodu wprowadza w błąd.
- **Rekomendacja:** Przenieść Nano Banana po enrich albo jawnie udokumentować, że jego wejściem jest wyłącznie narracja, i usunąć martwą gałąź.
- **Pewność:** [potwierdzone odczytem]

#### DS-012 — `--output-mode` nie steruje generowaniem plików
- **Waga:** ŚREDNIA
- **Plik:** `vtd/cli.py:60` (argument), `:1022-1025` (kopiowanie)
- **Cytat (odczyt):** argument `--output-mode` z helpem o internal/client-ready/both; kod bezwarunkowo tworzy katalogi `internal/` i `client-ready/` i kopiuje oba zestawy.
- **Opis:** Wartość trafia do metadata, ale nie wpływa na to, co zostaje wyprodukowane. Użytkownik wybierający „internal" nadal dostaje pliki client-ready.
- **Ryzyko:** Ryzyko wycieku materiału oznaczonego jako wewnętrzny; mylące CLI.
- **Rekomendacja:** Realnie warunkować generowanie/kopiowanie katalogów wg `output_mode` lub usunąć opcję i zaktualizować dokumentację.
- **Pewność:** [potwierdzone odczytem]

#### DS-013 — `numpy` używany bez deklaracji w zależnościach
- **Waga:** ŚREDNIA
- **Plik:** `vtd/core/frame_sharpness.py:10`, `pyproject.toml`
- **Cytat:** `import numpy as np`
- **Opis:** `numpy` nie występuje w `[project].dependencies` (wchodzi jedynie przechodnio, m.in. przez faster-whisper/imagehash). Przy zmianie wersji zależności może zniknąć.
- **Ryzyko:** `ImportError` w środowisku bez przechodniego numpy; niedeterministyczne instalacje.
- **Rekomendacja:** Dodać `numpy` jako jawną zależność (lub usunąć bezpośrednie użycie).
- **Pewność:** [potwierdzone odczytem]

#### DS-014 — Temp ZIP w `/tasks/{id}/download` bez `try/finally` (wyciek)
- **Waga:** ŚREDNIA
- **Plik:** `vtd/studio/app.py:421` i generator `_stream_and_cleanup` (~447)
- **Cytat (odczyt):** `tmp = tempfile.NamedTemporaryFile(..., delete=False)`; generator po odczytaniu robi `Path(tmp.name).unlink(missing_ok=True)` bez `try/finally`.
- **Opis:** Jeśli klient rozłączy się w trakcie strumieniowania, generator zostanie zamknięty przed linią `unlink`; plik tymczasowy pozostaje na dysku.
- **Ryzyko:** Akumulacja plików ZIP w katalogu temp przy dużym ruchu/rozłączeniach.
- **Rekomendacja:** Owinąć strumieniowanie w `try/finally` i usuwać plik także w `BackgroundTask`/`on_close`.
- **Pewność:** [potwierdzone odczytem]

#### DS-015 — `db.py` odtwarza połączenie/tabelę przy każdym wywołaniu; brak WAL/busy_timeout
- **Waga:** ŚREDNIA
- **Plik:** `vtd/studio/db.py` (`init_db` def w `:64`; wywołania `:75, :121, :132, :144, :163`)
- **Cytat (odczyt):** `init_db()` wołane na początku `create_task`, `get_task`, `list_tasks`, `list_tasks_by_status`, `update_task`.
- **Opis:** Każda operacja tworzy połączenie i wykonuje `CREATE TABLE IF NOT EXISTS`. Brak `PRAGMA journal_mode=WAL` i `busy_timeout`.
- **Ryzyko:** Overhead; przy współbieżności (np. wiele workerów/uvicornów) `database is locked`. `_lock` jest tylko procesowy.
- **Rekomendacja:** Jednorazowa inicjalizacja schema, współdzielone połączenie/pula, włączenie WAL i `busy_timeout`, migracje wersjonowane.
- **Pewność:** [potwierdzone odczytem]

#### DS-016 — Klient OpenAI ponawia błędy nietransientne (`except Exception`)
- **Waga:** ŚREDNIA
- **Plik:** `vtd/providers/openai_client.py` (`_execute_request`)
- **Cytat (odczyt):** `for attempt in range(self.max_retries + 1):` ... w `except Exception:` ustawiany `last_error` i ponowna próba; wewnątrz jawnie `raise ValueError(...)` gdy brak `choices`.
- **Opis:** Poza wyjątkami HTTP 429/5xx, ogólny `except Exception` ponawia także błędy programistyczne/parsowania (w tym celowy `ValueError`), marnując czas i mnożąc koszty.
- **Ryzyko:** Wolne działanie, wielokrotne płatne wywołania przy błędzie, mylące komunikaty.
- **Rekomendacja:** Ponawiać wyłącznie błędy przejściowe (sieć/429/5xx), rozdzielić walidację odpowiedzi od retry, dodać jitter i limit backoffu.
- **Pewność:** [potwierdzone odczytem]

#### DS-017 — `_load_env_file` mutuje globalne środowisko procesu
- **Waga:** ŚREDNIA
- **Plik:** `vtd/config.py` (`_load_env_file`, ~139-157)
- **Cytat (odczyt):** `if key and key not in os.environ: os.environ[key] = val; _dotenv_injected[key] = val`, całość w `except Exception: pass`.
- **Opis:** Wczytywanie `.env` ma efekt uboczny w postaci wpisów do `os.environ` całego procesu (dotyczy też importu `image_enhancer`). Błędy parsowania są cicho połykane; brak obsługi `export ` i komentarzy w linii.
- **Ryzyko:** Trudne do prześledzenia zależności konfiguracyjne, zaskakujące nadpisania, ukryte błędy literowe w `.env`.
- **Rekomendacja:** Zwracać dict zamiast mutować środowisko (lub robić to jawnie i udokumentować), raportować problematyczne linie.
- **Pewność:** [potwierdzone odczytem]

#### DS-018 — Watchdog zaszyty na CUDA/model/lokalny plik env
- **Waga:** ŚREDNIA
- **Plik:** `vtd/core/watchdog.py` (`scan_and_process_once`, `ENV_FILE`)
- **Cytat (odczyt):** `run_pipeline(device='cuda', model='large-v3', scene_threshold=0.05, track=..., enrich=True, ...)`; `ENV_FILE = env VTD_ENV_FILE or ~/.hermes/.env`.
- **Opis:** Watchdog na sztywno wymusza `device='cuda'` (bez fallbacku na CPU) i `large-v3`, oraz domyślnie czyta plik env z katalogu autora.
- **Ryzyko:** Na maszynie bez GPU watchdog będzie się wywalał; nieprzenośność instalacji.
- **Rekomendacja:** Uczynić device/model konfigurowalnymi, dodać fallback CPU, usunąć domyślną ścieżkę `~/.hermes`.
- **Pewność:** [potwierdzone odczytem]

#### DS-019 — Brak obsługi błędów ładowania modelu Whisper; brak pre-cache w obrazie
- **Waga:** ŚREDNIA
- **Plik:** `vtd/core/transcriber.py:25-33`, `Dockerfile`
- **Cytat (odczyt):** `WhisperModel(...)` konstruowany bez `try/except` po `import faster_whisper`.
- **Opis:** Pierwsze uruchomienie pobiera `large-v3` z sieci; brak sieci/uprawnień kończy się nieczytelnym wyjątkiem. Dockerfile nie pobiera modelu na etapie build.
- **Ryzyko:** Nieprzewidywalny first-run, długie pobieranie, błędy bez kontekstu.
- **Rekomendacja:** Owinąć ładowanie czytelnym błędem, udokumentować pobieranie, opcjonalnie pre-cache modelu w obrazie/wolumenie.
- **Pewność:** [potwierdzone odczytem]

#### DS-020 — `app.py` ufa `output_dir`/`log_path` z bazy (odczyt dowolnego pliku przy manipulacji DB)
- **Waga:** ŚREDNIA
- **Plik:** `vtd/studio/app.py` (~411 oraz endpoint loga)
- **Cytat (odczyt):** endpointy wyników/loga korzystają z `task['output_dir']` i `task['log_path']` zapisanych w SQLite.
- **Opis:** Dane z DB nie są ograniczane do dozwolonego katalogu. Przy możliwości zapisu do DB (lub błędzie) można wskazać dowolną ścieżkę systemową.
- **Ryzyko:** Odczyt plików poza sandboxem studia.
- **Rekomendacja:** Walidować `output_dir`/`log_path` względem skonfigurowanych katalogów (`Path.is_relative_to`) przed odczytem.
- **Pewność:** [potwierdzone odczytem]

#### DS-021 — Klucz API w query string Google + auto-akcept przy pustym OCR
- **Waga:** ŚREDNIA
- **Plik:** `vtd/core/image_enhancer.py:242`, `:369-371`
- **Cytat:** `url = f"{API_BASE}/{model}:generateContent?key={api_key}"`; `if not orig_norm: return True, "oryginał bez czytelnego tekstu (OCR puste) — brak czego chronić"`
- **Opis:** Klucz w URL może trafić do logów/proxy. Gdy tesseract jest niedostępny, OCR oryginału jest pusty → weryfikacja wierności tekstu automatycznie przepuszcza wynik (ciche osłabienie kontroli).
- **Ryzyko:** Wyciek klucza; brak realnej ochrony treści tam, gdzie OCR zawodzi.
- **Rekomendacja:** Przekazywać klucz nagłówkiem/poza query; rozróżnić „brak tekstu" od „OCR niedostępny" i traktować to drugie jako reject/warning.
- **Pewność:** [potwierdzone odczytem]

#### DS-022 — Dokument klienta zawiera zaszyty tekst „NVIDIA RTX 5070"
- **Waga:** ŚREDNIA
- **Plik:** `vtd/builders/html_builder.py:356`
- **Cytat:** `... model suggestion • NVIDIA RTX 5070`
- **Opis:** Stopka generowanego HTML (dokument dla klienta) zawiera informację o sprzęcie autora.
- **Ryzyko:** Nieprofesjonalny wyciek informacji o środowisku w materiale klienta.
- **Rekomendacja:** Usunąć zaszyty sprzęt lub zastąpić neutralnym opisem/produktem.
- **Pewność:** [potwierdzone odczytem]

#### DS-023 — Zależności bez górnych ograniczeń i bez użycia lockfile
- **Waga:** ŚREDNIA
- **Plik:** `pyproject.toml` (deklaracje `>=`), `Dockerfile` (`pip install .`)
- **Cytat (odczyt):** `pydantic>=2.0.0`, `pillow>=10.0.0`, ... `pyyaml>=6.0.0`; brak górnych granic; w repo istnieje `uv.lock`, ale Dockerfile go nie używa.
- **Opis:** Build obrazu rozwiązuje zależności „na świeżo", bez powtarzalności; brak wykorzystania locka.
- **Ryzyko:** Nagłe regresje po wydaniu nowej wersji zależności; niedeterministyczne buildy.
- **Rekomendacja:** Wprowadzić górne granice lub budować z lockfile (`uv sync --frozen`/`pip install -r` z eksportu locka).
- **Pewność:** [potwierdzone odczytem]

#### DS-024 — Worker nie przekazuje `--nano-banana` mimo kolumny w DB
- **Waga:** ŚREDNIA
- **Plik:** `vtd/studio/worker.py` (budowa `cmd`), `vtd/studio/db.py` (kolumna `nano_banana`)
- **Cytat (odczyt):** W konstrukcji `cmd` występuje `--output-mode` (189) i inne flagi; brak `--nano-banana`.
- **Opis:** Zadanie utworzone z `nano_banana=1` nie włącza tej funkcji w procesie CLI.
- **Ryzyko:** Cicha utrata funkcjonalności zgłoszonej przez użytkownika studia.
- **Rekomendacja:** Dodać przekazanie flagi (i `--nano-auto-accept`/`--nano-mode` jeśli dotyczy) oraz test worker-a obejmujący ten przypadek.
- **Pewność:** [potwierdzone odczytem]

#### DS-025 — Parsowanie `showinfo`/`fps_mode vfr` w frame_extractor jest kruche
- **Waga:** ŚREDNIA
- **Plik:** `vtd/core/frame_extractor.py` (`extract_scene_frames`)
- **Cytat (odczyt):** filtr `select='eq(n\,0)+gt(scene,THR)' ... showinfo` i `-fps_mode vfr`; dopasowanie linii po substringu `showinfo`; przy niezgodności liczby pts → `RuntimeError`.
- **Opis:** Zależność od formatu logu ffmpeg i wersji (`-fps_mode` wymaga nowszego ffmpeg). Próba odczytu brakujących pts przerywa cały pipeline.
- **Ryzyko:** Awaria na starszym/inny-locale ffmpeg; twarde zatrzymanie całego przebiegu.
- **Rekomendacja:** Używać stabilnego parsera (np. `-progress`/`ffprobe`), walidować próg scen, degradować łagodniej niż twardy `RuntimeError`.
- **Pewność:** [potwierdzone odczytem]

#### DS-026 — `_ocr_cache` bez limitu + pełny OCR/narracja wysyłane do zewnętrznego LLM
- **Waga:** ŚREDNIA
- **Plik:** `vtd/core/enricher.py` (modułowy `_ocr_cache`, `enrich_steps_with_llm`)
- **Cytat (odczyt):** cache na poziomie modułu kluczowany path+mtime; prompt zawiera OCR/narrację kroków wysyłane do providera.
- **Opis:** Cache rośnie bez ograniczenia w długo żyjącym procesie studia. „Local-first" nie chroni przed wysyłką treści do zewnętrznego API.
- **Ryzyko:** Wzrost pamięci; ryzyko prywatności/danych.
- **Rekomendacja:** Ograniczyć cache (LRU/max size), udokumentować jasno wysyłkę danych i dać opcję trybu lokalnego.
- **Pewność:** [potwierdzone odczytem]

### NISKIE

#### DS-027 — Warunek zawsze prawdziwy w `manual_builder`
- **Waga:** NISKA
- **Plik:** `vtd/builders/manual_builder.py:290`
- **Cytat:** `if s.frame_path.exists() or True:`
- **Opis:** `or True` czyni warunek bezsensownym; sugeruje niezakończoną naprawę.
- **Ryzyko:** Nieczytelny kod, możliwe błędy przy nieistniejących klatkach.
- **Rekomendacja:** Usunąć `or True` i obsłużyć brak klatki jawnie.
- **Pewność:** [potwierdzone odczytem]

#### DS-028 — `.env.example` reklamuje `?token=` usunięte w kodzie
- **Waga:** NISKA
- **Plik:** `.env.example:22-23`
- **Cytat:** `... wymagają nagłówka X-VTD-Token albo parametru ?token=.`
- **Opis:** Kod (GF-014) usunął obsługę `?token=` (test to potwierdza), dokumentacja nie.
- **Ryzyko:** Użytkownik stosuje nieistniejącą metodę auth → 401.
- **Rekomendacja:** Zaktualizować opis.
- **Pewność:** [potwierdzone odczytem]

#### DS-029 — Drift `config.example.yaml` vs `PipelineConfig`
- **Waga:** NISKA
- **Plik:** `config.example.yaml`, `vtd/config.py`
- **Cytat (odczyt):** przykład nie zawiera m.in. `min_step_seconds`, `silence_noise_db`, `mic_track_index`/`system_track_index`, `frame_qa`, `annotate`, `mode`; `scene_threshold: 0.05` vs help CLI „domyślnie 0.35".
- **Opis:** Przykład nie odzwierciedla pełnej konfiguracji; rozbieżność wartości domyślnych.
- **Ryzyko:** Mylące oczekiwania co do domyślnych zachowań.
- **Rekomendacja:** Zsynchronizować przykład i help teksty z modelem konfiguracji.
- **Pewność:** [potwierdzone odczytem]

#### DS-030 — CI: publish nie zależy od `lint`; akcje pinowane po tagu
- **Waga:** NISKA
- **Plik:** `.github/workflows/docker.yml`
- **Cytat (odczyt):** `build-and-push` z `needs: test`; akcje `checkout@v4`, `setup-python@v5`, `docker/*@v3/v5`; lint to osobny job.
- **Opis:** Błąd lintera nie blokuje publikacji obrazu; brak pinowania po SHA; brak skanowania bezpieczeństwa i bramki pokrycia.
- **Ryzyko:** Publikacja obrazu z błędami statycznymi/podatnościami.
- **Rekomendacja:** Dodać `needs: [test, lint]`, pinować akcje po SHA, dodać skan (np. Trivy) i ewentualnie próg pokrycia.
- **Pewność:** [potwierdzone odczytem]

#### DS-031 — Stale docstring odwołujący się do starego pakietu
- **Waga:** NISKA
- **Plik:** `vtd/core/rerender_docs.py:4-5`
- **Cytat:** `Usage: python -m video_to_manual.rerender_docs <output_dir>` / `Or: python tools/video_to_manual/rerender_docs.py <output_dir>`
- **Opis:** Odwołania do nieistniejącej nazwy/ścieżki.
- **Ryzyko:** Dezinformacja deweloperska.
- **Rekomendacja:** Zaktualizować docstring.
- **Pewność:** [potwierdzone odczytem]

#### DS-032 — Redundantny `except (ImportError, Exception)` i `candidates.index()`
- **Waga:** NISKA
- **Plik:** `vtd/core/frame_qa.py:116`, `:140`
- **Cytat:** `except (ImportError, Exception):`; `if candidates.index(res) + 1 >= reviewer.max_retries:`
- **Opis:** `Exception` już obejmuje `ImportError`; użycie `max_retries` jako limitu kandydatów to nieporozumienie semantyczne; `index()` jest O(n) i podatne na duplikaty ścieżek.
- **Ryzyko:** Nieczytelność, subtelne błędy przy duplikatach.
- **Rekomendacja:** Uprościć wyjątki, wprowadzić osobny limit kandydatów, iterować przez `enumerate`.
- **Pewność:** [potwierdzone odczytem]

#### DS-033 — `annotator.py`: nieużywana zmienna, sztywny font/badge
- **Waga:** NISKA
- **Plik:** `vtd/core/annotator.py:247`, `_get_font`, `numbered_badge`
- **Cytat:** `overlay_arr = overlay.copy()` (nieużywane); `_get_font` próbuje `DejaVuSans-Bold` z twardej ścieżki, w razie braku `load_default` (mały font bitmapowy); badge o stałym rozmiarze 22px.
- **Opis:** Resztki eksperymentów, nieprzenośny font, oznaczenia nie skalują się do rozmiaru obrazu.
- **Ryzyko:** Nieczytelne oznaczenia na dużych klatkach; brak fontu na części systemów.
- **Rekomendacja:** Usunąć martwy kod, dołączyć font lub skalować badge/tekst od rozmiaru obrazu.
- **Pewność:** [potwierdzone odczytem]

#### DS-034 — `fusion.py`: powtórzona detekcja luk i nakładanie tekstu przez tolerancję
- **Waga:** NISKA
- **Plik:** `vtd/core/fusion.py`
- **Cytat (odczyt):** dwa bloki liczące przerwy >8 s; przypisanie segmentów z tolerancją 1.5 s może przypisać ten sam segment do dwóch kroków.
- **Opis:** Złożone zagnieżdżone pętle, O(n²), potencjalne duplikowanie treści między krokami.
- **Ryzyko:** Zduplikowany/przypisany do złego kroku tekst; wydajność.
- **Rekomendacja:** Wyodrębnić jedną funkcję detekcji luk, rozstrzygnąć przypisanie segmentów do pojedynczego kroku.
- **Pewność:** [potwierdzone odczytem]

#### DS-035 — `cli.py`: zaszyty `max_reviews` i manipulacje `locals()`
- **Waga:** NISKA
- **Plik:** `vtd/cli.py:670` oraz ~856-857, ~943-969
- **Cytat:** `"max_reviews": 12,`; `if 'skipped_qa_steps' in locals():`
- **Opis:** Duplikacja stałej `MAX_REVIEWS`; sprawdzanie zmiennych przez `locals()` jest kruche.
- **Ryzyko:** Rozjazd wartości przy zmianie stałej; błędy przy refaktorze.
- **Rekomendacja:** Użyć stałej, zainicjalizować zmienne jawnie.
- **Pewność:** [potwierdzone odczytem]

#### DS-036 — Niespójność dokumentacji: Python 3.10+ vs 3.11 (Dockerfile)
- **Waga:** NISKA
- **Plik:** `README.md` (badge/prereqs), `Dockerfile`
- **Cytat (odczyt):** README deklaruje Python 3.10+; obraz bazowy `python:3.11-slim-bookworm`.
- **Opis:** Rozbieżność minimalnej wersji między dokumentacją i kontenerem.
- **Ryzyko:** Mylące oczekiwania środowiskowe.
- **Rekomendacja:** Ujednolicić wersję w obu miejscach.
- **Pewność:** [potwierdzone odczytem]

#### DS-037 — Dockerfile: brak pinowania digestu bazowego obrazu i pakietów apt
- **Waga:** NISKA
- **Plik:** `Dockerfile`
- **Cytat (odczyt):** `FROM python:3.11-slim-bookworm`; `apt-get install -y ffmpeg tesseract-ocr ...` bez wersji/digestu.
- **Opis:** Buildy nie są w pełni odtwarzalne; podatności zależą od dnia budowy.
- **Ryzyko:** Nieprzewidywalne buildy i bezpieczeństwo.
- **Rekomendacja:** Pinować obraz po digest i kluczowe pakiety; regularnie odświeżać.
- **Pewność:** [potwierdzone odczytem]

#### DS-038 — Brak endpointu uploadu i brak rate limitingu w Studiu
- **Waga:** NISKA
- **Plik:** `vtd/studio/app.py`
- **Cytat (odczyt):** endpointy obejmują listę nagrań, tworzenie zadań i pobieranie, ale nie ma uploadu; brak ograniczania częstości żądań.
- **Opis:** Zakres zadania wspomina „uploady", których w kodzie nie ma; brak ochrony przed nadużyciem API.
- **Ryzyko:** Ograniczona funkcjonalność; możliwość nadużycia przy ekspozycji sieciowej.
- **Rekomendacja:** Doprecyzować, czy upload jest w zakresie; dodać rate limiting/prostą ochronę API.
- **Pewność:** [potwierdzone odczytem; upload — brak w tym odczycie]

#### DS-039 — `settings_store` cicho odrzuca nieznane klucze; dowolny interpreter z pliku
- **Waga:** NISKA
- **Plik:** `vtd/studio/settings_store.py`
- **Cytat (odczyt):** `save_settings` zachowuje wyłącznie klucze z `DEFAULTS`; `pipeline_python` pozwala wskazać dowolny interpreter zapisany w JSON.
- **Opis:** Przyszłe/nieznane ustawienia są bez ostrzeżenia usuwane; `pipeline_python` to lokalna możliwość wykonania dowolnego binarium.
- **Ryzyko:** Utrata konfiguracji; wzrost powierzchni ataku przy dostępie do pliku ustawień.
- **Rekomendacja:** Walidować/ostrzegać o nieznanych kluczach; ograniczyć `pipeline_python` do whitelisty lub usunąć.
- **Pewność:** [potwierdzone odczytem]

#### DS-040 — Transkrypcja: `beam_size=5` i zaszyty język `pl`
- **Waga:** NISKA
- **Plik:** `vtd/core/transcriber.py`
- **Cytat (odczyt):** `model.transcribe(..., beam_size=5, vad_filter=True, vad_parameters=...)`, język domyślnie `pl`.
- **Opis:** `beam_size=5` jest wolny na CPU; język domyślny to polski (bez autodetekcji).
- **Ryzyko:** Wydłużony czas przetwarzania; błędna transkrypcja dla innych języków.
- **Rekomendacja:** Uczynić `beam_size`/język konfigurowalnymi, rozważyć autodetekcję.
- **Pewność:** [potwierdzone odczytem]

---

## 3. Pozytywy

- **Czytelny podział warstw:** `core/` (ekstrakcja, fuzja, QA, buildery), `builders/`, `studio/` (app/db/worker/settings), `providers/` — spójny, z jasnymi odpowiedzialnościami.
- **Konsekwentna higiena napraw** — oznaczenia `GF-xxx`/`VTD-xxx` przy kluczowych decyzjach ułatwiają śledzenie intencji i regresji.
- **Bezpieczeństwo subprocess:** polecenia ffmpeg/ffprobe/tesseract budowane są listowo (bez `shell=True`), co eliminuje klasyczną injekcję powłoki.
- **Parametryzacja SQL:** `db.py` używa placeholderów i whitelisty kolumn (`_ALLOWED_TASK_COLUMNS`), brak konkatenacji wartości do zapytań.
- **Ochrona przed path traversal:** `resolve_video_path` w `app.py` sprawdza `is_relative_to(root)` i blokuje ścieżki poza katalogiem nagrań (potwierdzone testami, w tym symlink escape).
- **Sanityzacja HTML:** `html_builder.esc()` + `sanitize.render_markdown_safe()` (markdown + bleach, whitelista tagów/atrybutów/protokołów); test regresyjny GF-001 potwierdza escaping `<script>`.
- **Porównanie tokenu w stałym czasie** (`hmac.compare_digest`) i świadome usunięcie `?token=` (brak wycieku do logów/historii).
- **Deterministyczne adnotacje** w `annotator.py` (PIL) oraz tryb LOCATED w `image_enhancer.py`, gdzie model tylko wskazuje współrzędne, a rysowanie robi kod — dobre oddzielenie generacji od renderowania.
- **Walidacja danych z modelu:** `robust_json_decode`, `_clamp01`, `_parse_bbox` (zakres 0..1, sensowność bbox, max bok 0.6), whitelista typów adnotacji.
- **Timeouty ffmpeg/ffprobe** wyliczane z długości mediów i ograniczane (`media_timeout`), plus twarde limity recenzji klatek (`MAX_REVIEWS`).
- **Sensowny zestaw testów** (114 przechodzi) obejmujący parser audio, dedup, fuzję, config, studio auth/traversal, worker recovery, watchdog; kilka testów to regresje do konkretnych `GF-xxx`.

---

## 4. Luki w testach (priorytetowo)

1. **Brak testu ścieżki `--annotate`** — to bezpośrednia przyczyna niewykrycia DS-001. Dodać test end-to-end, który asertuje niepusty `annotations_map` (lub sprawdza, że nie ma wyjątku przy realnym `plan_annotations`).
2. **Brak testu `--nano-banana`** — DS-011 (kolejność względem enrich) i DS-024 (brak flagi w workerze) są niewidoczne. Testować, że `target_description` używa danych enrichment i że worker przekazuje flagę.
3. **`test_watchdog.py` utrwala błędny kontrakt Telegram** — jawnie wymaga, by tokenu NIE było w URL i BYŁ w nagłówku. To odwrotnie niż wymaga API. Zastąpić testem weryfikującym poprawny URL `/bot<TOKEN>/sendDocument`.
4. **`test_frame_qa.py` nie sprawdza, czy używana jest wizja** — nie wykryje DS-007; dodać asercję wywołania wizyjnego (jeśli to zamierzone) lub usunąć fałszywe oczekiwanie.
5. **`test_worker.py` nie asertuje flag** innych niż `--client` i `--nano-banana`; brak pokrycia `--frame-qa`, `--output-mode`, timeoutu potomków (DS-006).
6. **`test_element_locator.py` nie testuje nazwy parametru** (`model` vs `llm_model`) — patrz DS-001; dodać test wywołania funkcji z argumentem słownikowym/pozycyjnym.
7. **Brak testu na wyciek temp ZIP** (DS-014) i na `finally` w strumieniowaniu.
8. **Brak testu `db.py` pod współbieżność** (WAL/lock, DS-015) — obecny test to CRUD jednosesyjny.
9. **Brak testu regresyjnego dla `.dockerignore`** (DS-004) — można dodać test/CI-check obecności `.env` w ignore.
10. **Brak testów integralności treści HTML/DOCX** (DS-022 sprzęt; DS-036/o `render_docx_manual` ciche `return` bez pliku przy braku python-docx).
11. **`test_annotator.py`** — asercje przepuszczają zarówno czerwony, jak i biały (badge), przez co kolizje kolorów nie są wychwytywane.

---

## 5. Rekomendowany plan naprawczy (kolejność; bez implementacji)

1. **[KRYTYCZNE, szybkie]** DS-001 — naprawić nazwę parametru `llm_model`→`model` i dodać test regresyjny `--annotate`; zawęzić `except` wokół adnotacji (DS-008).
2. **[WYSOKIE, bezpieczeństwo]** DS-004 — dopisać `.env`/`.env*` do `.dockerignore` i rozważyć whitelist plików kopiowanych do obrazu.
3. **[WYSOKIE]** DS-002 — poprawić URL Telegrama (`/bot<TOKEN>/...`), przenieść token poza `argv`, zaktualizować test tak, by odzwierciedlał realne API.
4. **[WYSOKIE]** DS-003 — usunąć zaszyty model QA i korzystać z konfiguracji.
5. **[WYSOKIE]** DS-006 — uruchamiać subprocess w nowej grupie i zabijać całą grupę przy timeoucie; test na osierocone dzieci.
6. **[WYSOKIE]** DS-007, DS-009 — ujednolicić semantykę QA (wizja albo zmiana nazwy) i przestać cicho nadpisywać `--host`.
7. **[ŚREDNIE]** DS-011, DS-012, DS-024 — uporządkować kolejność Nano Banana/enrich, realnie egzekwować `output_mode`, przekazać `--nano-banana` w workerze.
8. **[ŚREDNIE]** DS-014, DS-020 — dodać `try/finally` dla temp ZIP i walidację ścieżek z DB do katalogów studio.
9. **[ŚREDNIE]** DS-005, DS-018, DS-021, DS-022 — usunąć zależności od infrastruktury autora, klucz poza query, neutralna stopka.
10. **[ŚREDNIE]** DS-013, DS-015, DS-016, DS-017, DS-023 — porządki zależności/DB/retry/env/lockfile.
11. **[NISKIE]** DS-027 … DS-040 — sprzątanie kodu, dokumentacji i CI (m.in. `needs: [test, lint]`, pin akcji, skan).

---

## 6. Zakres wykonanych czynności (PRAWDA)

### Pliki faktycznie przeczytane w tej sesji (z zakresami)

**Konfiguracja / DevOps / dokumentacja:**
- `README.md` (1-243, pełny)
- `pyproject.toml` (1-62, pełny)
- `config.example.yaml` (1-42), `config.example.json` (1-34)
- `Dockerfile` (1-44, pełny)
- `docker-compose.yml` (1-26, pełny)
- `.github/workflows/docker.yml` (1-93, pełny) — `ls -la .github/workflows/` pokazał tylko ten plik
- `.env.example` (1-25), `.dockerignore` (1-17), `.gitignore` (1-57)

**Pakiet `vtd/`:**
- `vtd/__init__.py`, `vtd/__main__.py`, `vtd/defaults.py` (1-12), `vtd/config.py` (1-263, pełny)
- `vtd/cli.py` (1-1215, pełny w 3 partiach)

**`vtd/studio/`:** `app.py` (1-511), `db.py` (1-179), `worker.py` (1-292), `settings_store.py` (1-71), `sanitize.py` (1-49), `__init__.py`

**`vtd/providers/`:** `base.py` (1-40), `factory.py` (1-18), `openai_client.py` (1-212), `__init__`

**`vtd/core/`:** `frame_extractor.py` (107), `frame_sharpness.py` (111), `frame_qa.py` (145), `frame_sources.py` (31), `annotator.py` (340), `audio_detection.py` (356), `audio_extractor.py` (130), `transcriber.py` (51), `fusion.py` (319), `dedup.py` (52), `element_locator.py` (205), `image_enhancer.py` (1-719, pełny w 2 partiach), `enricher.py` (212), `qa_verifier.py` (75), `rerender_docs.py` (313), `watchdog.py` (246), `__init__.py` (50)

**`vtd/builders/`:** `__init__.py` (15), `manual_builder.py` (1-607, pełny), `html_builder.py` (1-364, pełny)

**`tests/`:** `conftest.py` (14), `test_annotator.py` (74), `test_audio_detection.py` (106), `test_builders.py` (66), `test_cli_pipeline.py` (150), `test_config.py` (93), `test_dedup.py` (77), `test_element_locator.py` (26), `test_enricher.py` (130), `test_frame_extractor.py` (82), `test_frame_qa.py` (26), `test_fusion.py` (80), `test_image_enhancer.py` (103), `test_openai_client.py` (72), `test_qa_verifier.py` (66), `test_rerender_docs.py` (91), `test_studio.py` (196), `test_transcriber.py` (73), `test_watchdog.py` (100), `test_worker.py` (158)

### Komendy faktycznie uruchomione (z wynikami)
- `git status` / `git log` — struktura repo, lista zmodyfikowanych i nietrackowanych plików.
- `ls -la .github/workflows/` — tylko `docker.yml`.
- `ls vtd/studio/templates` — `base.html, index.html, new.html, results.html, settings.html, task_detail.html`; `vtd/studio/static` — `app.js, style.css`.
- `uv run --extra dev python -m pytest tests/ -q` → **114 passed, 1 warning in 2.07s** (StarletteDeprecationWarning).
- `grep -n` celowane weryfikacje cytatów: `vtd/cli.py` (plan_annotations, model QA, max_reviews), `vtd/core/watchdog.py` (URL Telegram), `vtd/builders/html_builder.py:356`, `vtd/builders/manual_builder.py:290`, `vtd/core/frame_sharpness.py:10`, `vtd/core/rerender_docs.py:4-5`, `vtd/core/annotator.py:247`, `vtd/core/frame_qa.py:116/140`, `vtd/core/audio_detection.py` (sentinel -91).

### Czego NIE zrobiono (wprost)
- **Nie przeczytano w całości** szablonów `vtd/studio/templates/*.html` ani `vtd/studio/static/app.js`/`style.css` — tylko wylistowano. Znaleziska dotyczące UI (DS-010, CSRF/statyki) są oparte na odczytaniu kodu `app.py` i warstwy middleware, a nie na treści szablonów/JS.
- **Nie uruchomiono aplikacji** (studia, pipeline'u na prawdziwym video), nie testowano z ffmpeg/tesseract/Whisper na żywym pliku.
- **Nie uruchomiono** `ruff` ani innych linterów/analizy statycznej poza odczytem; nie zmierzono pokrycia kodu.
- **Nie porównano linia-po-linii** wszystkich niezacommitowanych zmian względem HEAD (analiza dotyczy stanu aktualnego, nie diffu).
- **Nie otwierano** plików `AUDYT_KODU.md`, `ANALIZA_*.md`, `NAPRAWY_*.md` jako źródła (zgodnie z regułami).
- **Nie wykonano żadnych edycji** poza niniejszym raportem; nie commitowano; nie ruszano `.env`.
- Weryfikacja cytatów `[potwierdzone odczytem]` pochodzi z odczytów i celowanych `grep` z tej sesji; pozycje oznaczone `[do weryfikacji]` nie wystąpiły — wszystkie znaleziska potwierdzono odczytem.

**Liczba znalezisk: 40** (DS-001 … DS-040).
