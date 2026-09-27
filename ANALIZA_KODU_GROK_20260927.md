# Analiza jakości kodu projektu video-to-doc (vtd)

Data: 2026-09-27. Analiza wykrywcza, niezależna (nie oparta na AUDYT_KODU.md).
Nie naprawiano kodu. Jedyny utworzony plik: ten raport.

## 1. Podsumowanie wykonawcze

Ocena: **6.8 / 10**.

Kod jest modularny (core / builders / studio / providers), studio ma parametryzowane SQL i walidację ścieżek nagrań, a kontrakt „zawsze DRAFT” jest konsekwentny. Po odczycie core ocena spada względem pierwszego szkicu: HTML budowany bez escape, watchdog z hardkodowaną ścieżką i domyślnym chat_id, krucha korekta liczb w QA, brak pełnego cleanup procesów dzieci w workerze, oraz luki testowe w fusion/dedup/watchdog/enricher/qa_verifier.

Najważniejsze wnioski:
1. `run_pipeline` w `vtd/cli.py` to jedna funkcja od linii 118 do końca pliku (~1000 linii) — wszystkie flagi (enrich, frame_qa, annotate, nano) w jednym bloku.
2. `vtd/core/watchdog.py` ma hardkodowany katalog nagrań i domyślny Telegram chat_id.
3. `vtd/builders/html_builder.py` wstawia metadane do HTML bez `html.escape`.
4. `vtd/core/qa_verifier.py` podmienia literał „5200” regexem przed audytem LLM.
5. Worker (`subprocess.Popen` bez nowej grupy procesów) przy timeout robi tylko `terminate`/`kill` na procesie rodzica.
6. Testy pokrywają studio, config, annotator, builders, openai_client, frame_qa, element_locator. Brak testów fusion, dedup, watchdog, enricher, qa_verifier.

## 2. Znaleziska

Cytaty pochodzą z odczytu w tej sesji. Linie podane według numeracji z narzędzia read.

### GR-001 — KRYTYCZNA — `vtd/cli.py:118`

`run_pipeline` zaczyna się w linii 118 i obejmuje ekstrakcję audio, transkrypcję, fuzję, Frame QA, adnotacje, nano-banana, enrich, QA, zapis MD/DOCX/HTML i manifest. Sygnatura ma ~25 parametrów (linie 118–143). Dodanie flagi wymaga edycji tej samej funkcji.

Ryzyko: regresje przy każdej zmianie pipeline.
Rekomendacja: rozbić na etapy (audio, frames, qa, annotate, docs) z jawnym obiektem stanu. Nie implementowano.

### GR-002 — WYSOKA — `vtd/studio/worker.py:219`

```python
proc = subprocess.Popen(
    cmd,
    stdout=subprocess.PIPE,
    stderr=subprocess.STDOUT,
    text=True,
    bufsize=1,
)
```

`_terminate_process` (linie 96–112) woła `proc.terminate()` / `proc.kill()` tylko na tym PID. Brak `start_new_session=True` / `os.setsid`. Timeout: `TASK_TIMEOUT_SECONDS = 4 * 60 * 60` (linia 27), potem `_terminate_process` (linie 258–263).

Ryzyko: procesy potomne (ffmpeg/whisper uruchomione przez `python -m vtd`) mogą przeżyć timeout rodzica.
Rekomendacja: nowa sesja procesów i zabicie grupy. Nie implementowano.

### GR-003 — ŚREDNIA — `vtd/studio/app.py:70-73` — do weryfikacji pod kątem realnego exploita

```python
resolved = candidate.resolve()
...
if not resolved.is_relative_to(root):
```

`Path.resolve()` rozwiązuje symlinki (dokumentacja pathlib). Odrzucenie ścieżki poza `RECORDINGS_DIR` jest zaimplementowane. Twierdzenie „pozwala symlinki wychodzące” z pierwszej wersji raportu **nie jest potwierdzone** tym fragmentem — `resolve()` + `is_relative_to` powinno je odciąć, o ile `root = RECORDINGS_DIR.resolve()` (linia 61) jest katalogiem docelowym. Brak testu w odczytanych plikach testów, który by to sprawdzał na żywym symlinku.

Ryzyko: niepotwierdzone bez testu; ewentualna luka jeśli `RECORDINGS_DIR` sam jest symlinkiem i porównanie jest niespójne.
Rekomendacja: test z symlinkiem wychodzącym poza katalog. Nie implementowano.

### GR-004 — ŚREDNIA — `vtd/providers/openai_client.py:118-119`

```python
if e.code in (429, 500, 502, 503, 504) and attempt < self.max_retries:
    time.sleep(1.5 ** attempt)
```

Backoff bez jittera. Ta sama formuła w linii 125 dla wyjątków ogólnych.

Ryzyko: zsynchronizowane retry przy 429.
Rekomendacja: jitter. Nie implementowano.

### GR-005 — NISKA — `vtd/studio/db.py:131-137`

`list_tasks` ma `LIMIT ?` (parametryzowane). `list_tasks_by_status` (linie 142–149) robi `SELECT * ... WHERE status = ?` **bez** limitu i `fetchall()`. `update_task` (linie 154–165) buduje `SET` z kluczy `**fields` — wartości są placeholderami `?`, ale **nazwy kolumn nie są walidowane** względem whitelist.

Ryzyko: przy wywołaniu `update_task` z kluczem spoza schematu — błąd SQL lub (gdyby klucz pochodził z zewnątrz) wstrzyknięcie w identyfikator. W odczytanym workerze klucze są literalami (`status`, `stage`, …), nie z requestu.
Rekomendacja: whitelist kolumn w `update_task`. Nie implementowano.

Korekta względem pierwszej wersji: GR-005 nie dotyczy braku limitu w `get_task` (linia 125 to `WHERE id = ?`).

### GR-006 — NISKA — `vtd/config.py:40-43`

```python
for env_var in ("OPENAI_API_KEY", "OPENROUTER_API_KEY", "OMNIROUTE_API_KEY"):
    val = os.environ.get(env_var, "")
```

`get_api_key()` zwraca pierwszy niepusty klucz z listy, niezależnie od `provider` / `api_key_env`.

Ryzyko: zły klucz przy kilku providerach w jednym procesie.
Rekomendacja: respektować wyłącznie `api_key` i `api_key_env`. Nie implementowano.

Zakres linii w pierwszej wersji („222”) był błędny — fallback jest w liniach 40–43. Linie 216–222 to override `base_url` / `api_key` z env.

### GR-007 — WYSOKA (luka testowa) — `tests/`

Odczytany glob `tests/*.py`: `test_config.py`, `test_annotator.py`, `conftest.py`, `test_builders.py`, `test_studio.py`, `test_openai_client.py`, `test_frame_qa.py`, `test_element_locator.py`.

Brak plików testowych dla: `qa_verifier.py`, `enricher.py`, `watchdog.py`, `fusion.py`, `dedup.py`, `transcriber.py`, `worker.py` (worker może być częściowo w `test_studio.py` — **nie weryfikowano treści test_studio.py w tej turze**).

Pierwsza wersja GR-007 („brak testów openai_client”) jest **fałszywa** — plik `tests/test_openai_client.py` istnieje. Treści asercji nie czytano.

### GR-008 — ŚREDNIA — `vtd/core/frame_qa.py:33-70` — częściowo niezweryfikowane jako „brak cache”

`review_step_frame` buduje prompt tekstowy i woła `provider.chat_completion` (nie `vision_completion`). Obraz nie jest wysyłany — tylko OCR z `extract_frame_ocr` (linie 41–47, 49–54). Brak memoizacji w tym pliku (każde wywołanie = nowe żądanie). To jest fakt z kodu. Koszt „wielu kroków” zależy od limitu `MAX_REVIEWS = 12` (linia 15) w CLI — sam moduł limitu nie egzekwuje.

Dodatkowy fakt: `improve_step_frame` linie 140–141:

```python
if candidates.index(res) + 1 >= reviewer.max_retries:
    break
```

`list.index` jest O(n) i przy zduplikowanych ścieżkach zwróci pierwsze wystąpienie. `max_retries` domyślnie 2 (linia 25) ucina pętlę kandydatów wcześniej niż liczba kandydatów `k=4`.

### GR-009 — ŚREDNIA — `Dockerfile:1-46`

Obraz `python:3.11-slim-bookworm`. Brak instrukcji `USER`. `HEALTHCHECK` jest (linie 41–42). `CMD ["studio", "--host", "0.0.0.0", ...]`.

Ryzyko: proces w kontenerze jako root.
Rekomendacja: użytkownik nieuprzywilejowany. Nie implementowano.

### GR-010 — NISKA — CI

`glob` `.github/**/*` oraz `.github/workflows/*` w katalogu projektu zwrócił **No files found**. Workflow docker **nie istnieje w drzewie**, które widzi narzędzie. Nie twierdzę, że nigdy nie był w zdalnym repo — lokalnie go nie ma.

### GR-011 — WYSOKA — `vtd/core/watchdog.py:15-17`

```python
RECORDINGS_DIR = Path("/home/kozlo/erp-workspace/recordings")
STATE_FILE = RECORDINGS_DIR / ".processed_recordings.json"
ENV_FILE = Path.home() / ".hermes" / ".env"
```

Ścieżka użytkownika jest stała. `load_env` czyta `~/.hermes/.env` (linie 23–31). Nie kopiuję wartości sekretów.

Ryzyko: moduł nie działa na innej maszynie; stan i nagrania są przywiązane do jednego katalogu.
Rekomendacja: `load_config().storage.recordings_dir`. Nie implementowano.

### GR-012 — WYSOKA — `vtd/core/watchdog.py:77-89`

```python
chat_id = env.get("HERMES_ARTIFACT_CHAT", "6989419513")
...
cmd = [
    "curl", "-s",
    "-F", f"chat_id={chat_id}",
    "-F", f"caption={caption}",
    "-F", f"document=@{docx_path}",
    f"https://api.telegram.org/bot{bot_token}/sendDocument"
]
```

Domyślny numeryczny chat_id w kodzie. Wysyłka jest za flagą `allow_telegram` (linie 168–186) — domyślnie wyłączona (`main`, linia 227). Mimo to identyfikator jest w repozytorium.

Ryzyko: ujawnienie identyfikatora czatu; `caption` idzie do argv curla (treść z nazw plików — nie z sieci w tym fragmencie).
Rekomendacja: brak domyślnego chat_id; odmowa wysyłki gdy env puste. Nie implementowano.

### GR-013 — WYSOKA — `vtd/builders/html_builder.py:45` oraz 116

```python
meta_items.append(f"<div><strong>{k}:</strong> {v}</div>")
```

`v` pochodzi z `metadata` (klient, proces, autor, …) i jest wstawiane do HTML bez `html.escape`. Linia 116:

```python
legend_html = f"<div ...>{es_info.get('annotation_legend')}</div>"
```

`annotation_legend` składa CLI z etykiet OCR/LLM (`vtd/cli.py` okolice 981–988 — odczytane wcześniej w sesji). Studio renderuje Markdown przez bleach (`sanitize.py`), ale ten builder zapisuje surowy plik HTML.

Ryzyko: stored XSS przy otwarciu `INSTRUKCJA.html`, jeśli metadane lub legenda zawierają HTML.
Rekomendacja: escape wszystkich interpolacji. Nie implementowano.

### GR-014 — ŚREDNIA — `vtd/core/qa_verifier.py:29-34`

```python
data_str = json.dumps(enriched_data, ensure_ascii=False)
if "5200" in data_str:
    issues_found.append("KOREKTA LICZBOWA: Skorygowano format liczbowy ze skali tysięcznej do bazowej.")
    data_str = re.sub(r"\b5200\b(?:\.00)?(?:\s*sztuk|\s*szt)?", "52 sztuki", data_str)
    enriched_data = json.loads(data_str)
```

Każde wystąpienie tokenu 5200 w całym JSON (także w innym kroku / w opisie niezwiązanym z ilością) uruchamia podmianę. Potem `json.loads` — przy nietypowym wyniku regexa może rzucić i przerwać audyt (brak try wokół `loads`).

Ryzyko: cicha, błędna korekta dokumentów; kruchość.
Rekomendacja: usunąć regułę albo ograniczyć do jawnego pola ilości. Nie implementowano.

### GR-015 — NISKA — `vtd/core/dedup.py:29` i `36`

```python
prev_hash = imagehash.phash(Image.open(frames[0].path))
...
curr_hash = imagehash.phash(Image.open(frame.path))
```

`Image.open` bez `with` / `close()`.

Ryzyko: deskryptory przy dużej liczbie klatek (CPython zwykle finalizuje, ale nie jest to gwarancja przy błędzie w pętli).
Rekomendacja: context manager. Nie implementowano.

### GR-016 — ŚREDNIA — `vtd/core/frame_qa.py:140`

Opisane przy GR-008. `candidates.index(res) + 1 >= reviewer.max_retries` miesza licznik retry sieci z liczbą kandydatów ostrości.

### GR-017 — NISKA — `vtd/core/transcriber.py:37-49`

```python
segments_generator, _ = self.model.transcribe(
    str(wav_path),
    language=language,
    beam_size=5,
    word_timestamps=True,
    ...
)
...
results.append(SpeechSegment(start=s.start, end=s.end, text=text))
```

`word_timestamps=True`, ale `SpeechSegment` nie ma pola słów. CLI w raporcie kompletności samo pisze, że word-level nie jest używany (odczyt `cli.py` wcześniej w sesji).

Ryzyko: zbędny koszt ASR. Nie jest to błąd poprawności.
Rekomendacja: wyłączyć albo zapisać słowa. Nie implementowano.

### GR-018 — NISKA — `vtd/core/enricher.py:24-33`

```python
res = subprocess.run(
    ["tesseract", str(frame_path), "stdout", "-l", "pol+eng", "--psm", "6"],
    capture_output=True, text=True, timeout=5,
)
...
return "\n".join(lines[:20])
```

Brak sprawdzenia `returncode`. Timeout 5 s jest. Wyjątki połykane (`return ""`).

Ryzyko: puste OCR mylone z brakiem tekstu; każdy krok enrichera odpala tesseract synchronicznie (linia 149) — przy wielu krokach czas się sumuje.
Rekomendacja: logować returncode; nie połykać timeout bez śladu w wyniku. Nie implementowano.

### GR-019 — ŚREDNIA — `vtd/core/fusion.py:69-70` i `139-153`

```python
if not frames:
    return []
```

Pusta lista klatek → brak kroków, nawet gdy transkrypcja istnieje. Pauza > 8 s jest literałem (linia 147: `if gap > 8.0:`), nie parametrem `min_step_seconds` / `max_step_seconds`.

Ryzyko: nagranie bez zmian sceny daje pusty dokument mimo mowy; próg 8 s niekonfigurowalny.
Rekomendacja: krok fallback z transkrypcji; próg pauzy w `PipelineConfig`. Nie implementowano. Dalsza logika podziału (od linii 185) **ucięta przy odczycie** — nie oceniam jej poprawności.

### GR-020 — NISKA — `vtd/studio/worker.py` (koniec odczytanego zakresu, linie 202–213)

Flaga `--nano-banana` **nie** jest dokładana do `cmd`, mimo że `create_task` w `app.py` zapisuje `nano_banana` (odczyt `app.py` linie 224–259). W `_execute_task` są `--enrich`, `--frame-qa`, `--annotate`, nie ma `--nano-banana`.

Ryzyko: opcja Studio „nano” nie dochodzi do pipeline. Potwierdzenie: odczyt `worker.py` do linii 292 (koniec pliku wg odczytu). Jeśli plik ma dalsze linie poza 292, **do weryfikacji** — odczyt zakończył się na linii 292 returncode error.
Rekomendacja: sprawdzić, czy pole ma być celowo ignorowane; jeśli nie — dodać flagę. Nie implementowano.

## 3. Pozytywy

- Warstwy `core`, `builders`, `studio`, `providers` są rozdzielone; provider LLM jest jednym klientem OpenAI-compatible (`factory.py`).
- SQL w `db.py` używa placeholderów `?` dla wartości (`create_task`, `get_task`, `list_tasks`).
- `resolve_video_path` (`app.py` 52–80) wymaga pliku wewnątrz `RECORDINGS_DIR` po `resolve()`.
- Auth: middleware `X-VTD-Token` gdy ustawiony `VTD_AUTH_TOKEN`; `/health` wyjęty (`app.py` 30–49).
- Markdown w studio przechodzi przez bleach (`sanitize.py` 35–49), protokoły ograniczone do http/https/mailto.
- Worker: timeout zadania, `terminate`+`kill`, recovery `running` → `error` po restarcie (`worker.py` 72–93).
- Kontrakt DRAFT: `html_builder.py` linie 19–23 zamieniają wartość `FINAL`; watchdog domyślnie nie wysyła Telegrama.
- `dedup.py` przy braku Pillow zwraca klatki zamiast rzucać (linie 22–26).
- Dockerfile ma HEALTHCHECK i czyści listy apt.

## 4. Luki w testach

Priorytet (na podstawie listy plików, nie coverage narzędziem — coverage **nie uruchamiano**):

1. `qa_verifier.audit_and_fix_manual` — regex „5200” i ścieżka wyjątku `json.loads`.
2. `html_builder` — metadane z `<script>` (czy test_builders to łapie: **nie sprawdzano treści**).
3. `watchdog` — brak pliku testowego; hardkodowana ścieżka.
4. `fusion.fuse_signals_into_steps_v2` — puste klatki, pauza > 8 s, min_step.
5. `dedup.filter_duplicate_frames` — próg Hamminga, `min_time_delta`.
6. `enricher.enrich_steps_with_llm` — brak klucza / błąd providera → None.
7. `worker._execute_task` — czy `nano_banana` trafia do argv; timeout zabija grupę procesów.
8. `resolve_video_path` — symlink wychodzący poza `RECORDINGS_DIR`.

Istnieją testy: config, annotator, builders, studio, openai_client, frame_qa, element_locator.

## 5. Plan naprawczy (kolejność, bez implementacji)

1. Escape HTML w `html_builder.py` (GR-013).
2. Usunąć domyślny chat_id i hardkodowany `RECORDINGS_DIR` z watchdoga (GR-011, GR-012).
3. Grupa procesów w workerze (GR-002) i decyzja ws. `--nano-banana` (GR-020).
4. Usunąć lub zawęzić regułę „5200” (GR-014).
5. Rozbić `run_pipeline` (GR-001) — dopiero po testach charakteryzujących obecne zachowanie.
6. Jitter w retry (GR-004), zamknięcie obrazów (GR-015), whitelist kolumn (GR-005).
7. Testy z sekcji 4.
8. `USER` w Dockerfile (GR-009) i przywrócenie workflow CI, jeśli ma być w repo (GR-010).

## 6. Zakres czynności (fakty)

Przeczytane w całości lub do wskazanego limitu:
- `README.md`, `pyproject.toml`, `vtd/config.py`, `vtd/__init__.py`, `vtd/__main__.py`
- `vtd/cli.py` linie 1–1113 (koniec pliku nieosiągnięty — odczyt urwał się przy 1113 z komunikatem kontynuacji; linie 1114+ **nie czytane**)
- `vtd/studio/app.py` (do 465, koniec odczytu), `db.py` (koniec przy 168), `worker.py` (koniec przy 292), `settings_store.py`, `sanitize.py`
- `vtd/providers/base.py`, `factory.py`, `openai_client.py`
- `vtd/core/frame_qa.py`, `enricher.py`, `qa_verifier.py`, `watchdog.py`, `transcriber.py`, `dedup.py`
- `vtd/core/fusion.py` linie 1–200 (dalej **nie czytane**)
- `vtd/builders/html_builder.py` linie 1–150 (dalej **nie czytane**), `manual_builder.py` linie 1–100 (dalej **nie czytane**)
- `Dockerfile`, `docker-compose.yml`

Nie czytane w tej sesji (nie oceniane): `frame_extractor.py`, `frame_sharpness.py`, `annotator.py`, `audio_detection.py`, `audio_extractor.py`, `element_locator.py`, `image_enhancer.py`, `rerender_docs.py`, `frame_sources.py`, treść plików w `tests/`, `config.example.yaml`.

Glob: `.github/**/*` — brak plików.

**pytest: nie uruchamiano** w tej turze. Liczba 35 passed z pierwszej odpowiedzi **nie jest** wynikiem komendy wykonanej przy pisaniu tego pliku. Nie powtarzam jej jako faktu.

Nie modyfikowano kodu projektu, nie było commita, nie czytano `.env`.
