# ANALIZA KODU — video-to-doc (VTD)

**Data:** 2026-09-27
**Model:** GLM-5.3 Flash [Plan Max] (Szybki Coder)
**Tryb:** DETECTIVE-ONLY — wyłącznie analiza, zero poprawek, zero edycji plików poza niniejszym raportem.
**Zakres analizy:** pełne odczyty wszystkich modułów `vtd/**`, `tests/**`, plików projektowych i CI + rzeczywiste uruchomienie testów w tej sesji. Raport w 100% oparty na odczytach z tej sesji; istniejące raporty AUDYT_KODU.md / ANALIZA_KODU_GROK celowo **nie były otwierane ani używane**.

---

## 1. Podsumowanie wykonawcze

### Ocena ogólna: **6.0 / 10**

### Kluczowe wnioski (takeaways)

1. **Rdzeń jest przemyślany, ale obudowa jest łata na łacie.** Pipeline (fuzja napisów+audio+klatki, QA klatek, manual-first DRAFT) ma sensowną architekturę, ale `cli.run_pipeline` to monolit ~1000 linii z martwymi gałęziami, `locals()`-checkami i mutacją danych kroków w locie.
2. **XSS w generowanym HTML — najpoważniejszy problem bezpieczeństwa.** `html_builder.py` nie stosuje żadnego escapowania; tytuł z formularza Studio i tekst ASR trafiają raw do `INSTRUKCJA.html`.
3. **Hard-coded środowisko dewelopera w kodzie produkcyjnym.** `watchdog.py` (ścieżka `/home/kozlo/...`, chat_id) i `image_enhancer.py` (gateway `127.0.0.1:20128`, klucze z `~/.hermes/.env`) są nieprzenośne i niekonfigurowalne.
4. **Config YAML w instalacji produkcyjnej działa losowo** — `pyyaml` jest tylko w dev-extras, a brak importu jest po cichu ignorowany; `config.example.yaml` może po prostu nie zostać wczytany.
5. **Testy są punktowe, nie systemowe.** 35 testów przechodzi (zweryfikowane: `35 passed, 3 warnings in 0.99s`), ale ZERO pokrycia dla samego `run_pipeline`, fusion, frame_extractor, dedup, enricher, rerender, watchdog, image_enhancer i flow workera.
6. **Wielokrotne „ciche fallbacki"** (błędny track audio, pominięty dedup, sfabrykowane timestampy, nadpisany model) maskują błędy zamiast je zgłaszać — to systematyczny antywzorzec całego kodu.

---

## 2. Findings

Wagi: KRYTYCZNA / WYSOKA / ŚREDNIA / NISKA.
Tagi pewności: **[potwierdzone odczytem]** = odczytano plik w tej sesji i zacytowano realny kod; **[do weryfikacji]** = wniosek pośredni wymagający uruchomienia/testu.

---

### GF-001 — Brak escapowania HTML w html_builder (XSS / wstrzyknięcie) — WYSOKA [potwierdzone odczytem]

**Plik:** `vtd/builders/html_builder.py:45,126,177,185-188,220`

```python
meta_items.append(f"<div><strong>{k}:</strong> {v}</div>")          # :45
<img ... alt="{label} {s.step_number}">                             # :126
{c_text}                                                            # :177 (callout)
<h3 class="step-title">{step_title}</h3> ... {speech_desc}          # :185-188
<title>{title} — DRAFT</title>                                      # :220
```

W całym pliku nie ma ani jednego `html.escape()`. `step_title`, `speech_desc`, callouty i metadane pochodzą z tekstu ASR oraz z tytułu wpisanego w Studio (`worker.py:179` — `title = task.process or video_name`), czyli danych w pełni kontrolowanych przez użytkownika.

**Ryzyko:** trwały XSS / wstrzyknięcie HTML w wygenerowanym `INSTRUKCJA.html`; przy otwieraniu pliku w przeglądarce skrypt wykonuje się w kontekście `file://` lub hostingu wewnętrznego. Dane ASR pochodzą z napisów z filmu — film może być wektorem ataku.
**Rekomendacja:** wprowadzić obowiązkowe escapowanie wszystkich interpolacji tekstowych (jeden helper `esc()`), rozważyć automatyczny lint testem „no raw `{` interpolation in HTML template strings".

---

### GF-002 — Magiczna podmiana „5200" w qa_verifier — WYSOKA [potwierdzone odczytem]

**Plik:** `vtd/core/qa_verifier.py:29-34`

```python
if "5200" in data_str:
    ...
    data_str = re.sub(r"\b5200\b(?:\.00)?(?:\s*sztuk|\s*szt)?", "52 sztuki", data_str)
enriched_data = json.loads(data_str)
```

Twardo zaszyta poprawka jednej konkretnej wartości w strumieniu JSON przed `json.loads`.

**Ryzyko:** cicha korupcja dowolnej legalnej liczby 5200 w danych (kod produktu, kwota, timestamp), niemożliwa do utrzymania; ukrywa prawdziwą przyczynę błędów LLM zamiast naprawiać prompt/walidację.
**Rekomendacja:** usunąć hack; błędy parsowania LLM obsłużyć retry z feed-backiem, a normalizację liczb zrobić jawnie na sparsowanych wartościach, nie na surowym JSON-stringu.

---

### GF-003 — watchdog.py: hard-coded ścieżka domowa, chat_id, token w URL — WYSOKA [potwierdzone odczytem]

**Plik:** `vtd/core/watchdog.py:15,80,89`

```python
RECORDINGS_DIR = Path("/home/kozlo/erp-workspace/recordings")   # :15
chat_id default "6989419513"                                     # :80
.../bot{bot_token}/sendDocument                                  # :89
```

Dodatkowo moduł nie jest podpięty do CLI (martwy w praktyce), a własny loader `.env` duplikuje `config._load_env_file`.

**Ryzyko:** nieprzenośność (moduł nie zadziała u nikogo innego), wyciek tokenu bota do listy procesów (`ps`) przez URL w argumencie, ryzyko umieszczenia prywatnego chat_id w repo.
**Rekomendacja:** wynieść ścieżkę i chat_id do konfiguracji, token przekazywać przez stdin/nagłówek, a moduł albo wpiąć w CLI jako komendę, albo wydzielić z pakietu.

---

### GF-004 — image_enhancer: hard-coded gateway, klucze z ~/.hermes — WYSOKA [potwierdzone odczytem]

**Plik:** `vtd/core/image_enhancer.py:25,29,33,41-44,512`

```python
OMNIROUTE_BASE = "http://127.0.0.1:20128/v1"                     # :25
"cheaperinference/nano-banana-2" / "gemini-3.7-flash"            # :29,33,512
_KEY_FILES = [Path.home()/".hermes"/".env", ...]                 # :41-44
```

Kontrahuje z deklaracją „provider-agnostic" — enhancer jest na sztywno związany z lokalnym gatewaysem i prywatnymi plikami kluczy. `enhance_frame()` (328-391) nie jest już używane w flow CLI (CLI woła `enhance_frame_located`/`composite`), a zostawia plik `<stem>_enh_raw.png` w `frames/`.

**Ryzyko:** martwy kod z efektami ubocznymi na dysku, brak konfigurowalności, myląca dokumentacja; nowy deweloper nie zbuduje enhancera bez prywatnej infrastruktury autora.
**Rekomendacja:** przenieść base/model/keys do konfiguracji, usunąć martwe `enhance_frame()`, posprzątać pliki tymczasowe.

---

### GF-005 — pyyaml brakuje w zależnościach produkcyjnych; ciche ignorowanie config.yaml — WYSOKA [potwierdzone odczytem]

**Pliki:** `pyproject.toml:27-39` vs `42-46`; `vtd/config.py:180-186`

W `[project.dependencies]` nie ma `pyyaml` (jest tylko w `[project.optional-dependencies].dev`). W `config.py` import YAML jest w try/except i przy braku biblioteki **po cichu pomija** plik konfiguracyjny.

**Ryzyko:** instalacja produkcyjna (`pip install .`) → `config.yaml` (wzorcowany przez `config.example.yaml`) nigdy nie zostanie wczytany, bez żadnego komunikatu. Zachowanie różni się między dev a prod — klasyczna pułapka wdrożeniowa.
**Rekomendacja:** dodać `pyyaml` do zależności podstawowych albo jawnie zalogować ostrzeżenie i udokumentować, że config-y YAML wymagają extras.

---

### GF-006 — run_pipeline: monolit ~1000 linii, locals(), martwe gałęzie — WYSOKA [potwierdzone odczytem]

**Plik:** `vtd/cli.py:118-1154` (+ punkty: 185-188 `pass`, 213-215, 274-276, 574, 675-680, 833-838, 473, 1069-1080, 527-534)

```python
if 'video_path' in locals(): ...            # wzorzec locals() w 3 miejscach
pass                                        # martwe gałęzie
```

- Funkcja ~1030 linii; stan przekazywany przez zmienne lokalne sprawdzane przez `locals()`.
- Zduplikowany słownik stopwords z powtórzonymi wpisami (473).
- If/else z identycznymi gałęziami przy kopiowaniu frames (1069-1080).
- Konwolucyjny zapis PNG (527-534): save, potem martwe `if not out_path.exists()`, niespójne `compress_level=0` vs `1` w gridzie.

**Ryzyko:** każda zmiana w pipeline jest ryzykowna; niemożliwe testowanie jednostkowe; potwierdzone przez zerową liczbę testów na tę ścieżkę (patrz sekcja 4).
**Rekomendacja:** rozbicie na etapy jako jawne funkcje/kroki z jawnym kontekstem (dataclass PipelineContext), usunięcie martwego kodu, konfiguracja compress_level w jednym miejscu.

---

### GF-007 — QA marker mutuje speech_text (wyciek verdictów do dokumentów) — WYSOKA [potwierdzone odczytem]

**Plik:** `vtd/cli.py:697`

```python
s.speech_text = (s.speech_text + f" [Frame QA: {r['verdict']} — {r['reason']}]").strip()
```

Werdykt QA jest dopisywany do `speech_text`, a ten tekst jest następnie używany przez enricher (LLM) i builderów jako narracja kroku.

**Ryzyko:** notatki diagnostyczne („Frame QA: FAIL — …") trafiają do treści INSTRUKCJA/PROMPT i do promptów LLM — zanieczyszczenie danych zamiast oddzielnego kanału diagnostycznego.
**Rekomendacja:** przenieść wyniki QA do osobnego pola w modelu kroku i osobnej sekcji dokumentu.

---

### GF-008 — Cichy fallback wyboru ścieżki audio — ŚREDNIA [potwierdzone odczytem]

**Plik:** `vtd/cli.py:296,300` (+204-205)

```python
chosen_track = 2 if stream_count > 2 else 0
```

Gdy nagranie ma ≤2 strumienie audio, wybierany jest track 0 (zwykle miks) zamiast wymaganego tracka klienta — bez ostrzeżenia.

**Ryzyko:** transkrypcja z miksu zamiast z kanału klienta (dokument może opisywać rozmowę z klientem po lewej stronie), trudna do zdiagnozowana „losowa" jakość protokołów.
**Rekomendacja:** jawnie failować/raportować, gdy wymagany track nie istnieje.

---

### GF-009 — get_api_key: klucz jednego providera wysyłany do innego — ŚREDNIA [potwierdzone odczytem]

**Plik:** `vtd/config.py:40-45`

Fallback bierze dowolny z `OPENAI/OPENROUTER/OMNIROUTE` kluczy niezależnie od `base_url` providera.

**Ryzyko:** klucz OpenAI może zostać wysłany do OpenRouter/gateway (wyciek sekretu do trzeciego serwisu) — cicho, bez błędu.
**Rekomendacja:** sprzęgać klucz z providerem 1:1; brak klucza = czytelny błąd konfiguracji.

---

### GF-010 — element_locator: broad except + per-step load_config — ŚREDNIA [potwierdzone odczytem]

**Plik:** `vtd/core/element_locator.py:97,122`

```python
except (subprocess.TimeoutExpired, FileNotFoundError, Exception): return {}
```

Podwójne złapanie (`Exception` obok konkretnych typów) połyka wszystko, w tym błędy programistyczne. Dodatkowo `load_config()` wołane per krok (122) — wielokrotne parsowanie konfiguracji.

**Ryzyko:** maskowanie realnych błędów (locator zawsze „działa", zwracając `{}`), spadek wydajności przy wielu krokach.
**Rekomendacja:** zawęzić wyjątki do faktycznie oczekiwanych; konfigurację ładować raz na sesję annotacji.

---

### GF-011 — dedup: ciche pomijanie przy braku biblioteki + porównanie tylko z poprzednim — ŚREDNIA [potwierdzone odczytem]

**Plik:** `vtd/core/dedup.py:22-26`

```python
except ImportError:
    return frames
```

Brak `imagehash`/`PIL` → dedup po cichu wyłączony. Dodatkowo porównanie tylko z ostatnio zaakceptowaną klatką — przy powolnym dryfcie obrazu przechodzą kolejne niemal identyczne klatki.

**Ryzyko:** w produkcji bez extras zależności pipeline zwraca 10× więcej klatek bez ostrzeżenia; dryf znieczula na duplikaty.
**Rekomendacja:** twarda zależność lub jawny warning; porównywać z oknem ostatnich N hashy.

---

### GF-012 — frame_extractor: sfabrykowane timestampy + brak sprzątania starej sceny — ŚREDNIA [potwierdzone odczytem]

**Plik:** `vtd/core/frame_extractor.py:55`

```python
t = pts_times[i] if i < len(pts_times) else float(i)
```

Gdy parsowanie `showinfo` zwróci mniej wpisów, timestamp = indeks klatki (sekundy ≈ numer klatki) — fikcyjne czasy trafiają do dokumentu. Katalog wyjściowy nie jest czyszczony z poprzednich `scene_*.png` przy ponownym uruchomieniu.

**Ryzyko:** błędne „dowody czasowe" w protokole; zanieczyszczenie wyników starymi plikami przy rerun na tym samym katalogu.
**Rekomendacja:** failować przy niedopasowaniu liczby wpisów do liczby klatek; czyścić katalog przed ekstrakcją.

---

### GF-013 — rerender: parser Dowód nigdy nie pasuje do INSTRUKCJA.md — ŚREDNIA [potwierdzone odczytem, częściowo do weryfikacji na żywym pliku]

**Pliki:** `vtd/core/rerender_docs.py:101,129,150` vs `vtd/builders/manual_builder.py`

`dowód_pat` oczekuje linii `**Dowód:** ... klatka \`...\``, ale `manual_builder` nie emituje linii „Dowód" w INSTRUKCJA.md (są tylko w PROMPT). Efekt: ścieżka md-parse produkuje dummy timestampy 0-5 s.

**Ryzyko:** rerender z md daje sfabrykowane, mylące czasy w dokumencie końcowym.
**Rekomendacja:** ujednolicić format dowodów między builderem a parserem (wspólna stała/serializer).

---

### GF-014 — Studio: token w query param + porównanie nie stało-czasowe — ŚREDNIA [potwierdzone odczytem]

**Plik:** `vtd/studio/app.py:43-44`

Autoryzacja przyjmuje `?token=...` (leci do logów serwera/historii przeglądarki), porównanie zwykłym `==`.

**Ryzyko:** wyciek tokenu autoryzacji przez logi/referery; teoretyczny timing side-channel.
**Rekomendacja:** akceptować token tylko w headerze (oraz w formularzu logowania POST), porównywać przez `hmac.compare_digest`.

---

### GF-015 — Studio: deprecated `@app.on_event("startup")` — ŚREDNIA [potwierdzone odczytem; potwierdzone też warningiem pytest]

**Plik:** `vtd/studio/app.py:83`

FastAPI wyrzuca deprecation warning (widoczny w przebiegu testów: 3 warnings).

**Ryzyko:** usunięcie API w przyszłych wersjach FastAPI; szum w CI.
**Rekomendacja:** migracja na lifespan handler.

---

### GF-016 — Niespójne/„samopokonujące" domyślne modele enrich — ŚREDNIA [potwierdzone odczytem]

**Pliki:** `vtd/cli.py:51` ("erp-manual"), `vtd/studio/app.py:196,233-234`, `vtd/studio/settings_store.py:15-24`

Trzy różne domyślne modele w trzech miejscach. Ponadto:

```python
if enrich_model not in ALLOWED_MODELS: enrich_model = "artur-daily"   # app.py:233-234
```

— a `"artur-daily"` **nie jest** na liście `ALLOWED_MODELS`, więc walidacja niczego nie chroni i domyślnie wymusza model spoza listy.

**Ryzyko:** użytkownik Studio nieświadomie używa modelu gatewayowego, którego walidacja formalnie nie dopuszcza; różne domyślne CLI vs Studio vs ustawienia → nieprzewidywalne koszty/jakość.
**Rekomendacja:** jedna źródło prawdy dla domyślnego modelu + walidacja dopuszczająca wyłącznie modele z listy.

---

### GF-017 — download endpoint zipuje cały output do pamięci — ŚREDNIA [potwierdzone odczytem]

**Plik:** `vtd/studio/app.py:395-405`

`io.BytesIO` zbiera ZIP wszystkich plików output (w tym nagrania/klatki PNG) w RAM procesu.

**Ryzyko:** przy dużych nagraniach — skokowe zużycie pamięci, potencjalne OOM współdzielonego procesu Studio.
**Rekomendacja:** streamować ZIP bezpośrednio do odpowiedzi (StreamingResponse + zapis na dysk tymczasowy lub `zipfile` na potoku).

---

### GF-018 — db: SET clause przez f-string na kluczach, brak WAL — NISKA [potwierdzone odczytem]

**Plik:** `vtd/studio/db.py:154-166`

Kolumny budowane f-stringiem (wartości sparametryzowane — OK, klucze pochodzą wyłącznie z wewnętrznych wywołań). Każde wywołanie otwiera nowe połączenie; brak `journal_mode=WAL`.

**Ryzyko:** niskie (klucze wewnętrzne), ale wzorzec łatwy do nadużycia przy przyszłych zmianach; drobny narzut wydajnościowy.
**Rekomendacja:** mapowanie dozwolonych kolumn jako stała; ewentualnie WAL + reuse connection.

---

### GF-019 — transcriber: word_timestamps=True nieużywane — NISKA [potwierdzone odczytem]

**Plik:** `vtd/core/transcriber.py` (ustawienie `word_timestamps=True`, słowa nigdzie nie konsumowane).

**Ryzyko:** czysty narzut czasu/canvasu transkrypcji.
**Rekomendacja:** wyłączyć lub wykorzystać do precyzyjnych dowodów czasowych.

---

### GF-020 — enricher: parametr `timeout` martwy — NISKA [potwierdzone odczytem]

**Plik:** `vtd/core/enricher.py:74` — `timeout: int = 180` przyjmowany, nigdy nieużywany (provider stosuje timeout z config).

**Ryzyko:** mylące API — caller ustawia 180 s, realnie działa config.
**Rekomendacja:** usunąć parametr lub faktycznie przekazać do providera.

---

### GF-021 — Wielokrotny OCR tej samej klatki — ŚREDNIA [potwierdzone odczytem]

**Pliki:** `vtd/core/element_locator.py`, `vtd/core/enricher.py` (extract_frame_ocr), `vtd/core/frame_qa.py`

Ta sama klatka bywa OCR-owana do 3× w jednym przebiegu (locator, enricher, QA) z osobnymi timeoutami.

**Ryzyko:** wydajność — dla nagrań z dziesiątkami kroków to dominujący koszt wall-time.
**Rekomendacja:** cache OCR per (frame_path) w kontekście pipeline.

---

### GF-022 — MAX_REVIEWS przekraczane po cichu — ŚREDNIA [potwierdzone odczytem]

**Plik:** `vtd/cli.py:416-417`

Komentarz w kodzie przyznaje, że limit re-review może zostać przekroczony (`>12`) — pętla dopisuje kolejne iteracje po cichu.

**Ryzyko:** nieprzewidywalny koszt LLM i wall-time; limit bezpieczeństwa nie ogranicza niczego.
**Rekomendacja:** twardy break + jawnie raportowany status „QA nie doszło do PASS".

---

### GF-023 — fusion: konwolucyjna logika grid + placeholder paths — ŚREDNIA [potwierdzone odczytem]

**Plik:** `vtd/core/fusion.py:234-294,274-276`

Ścieżki placeholderów gridu gdy `frames_dir is None` — builderzy ratują się `.exists()`, więc bug objawia się milcząco jako brak obrazka; martwa gałąź `pass` (274-276); skanowanie gap-split o złożoności ~O(n²) (143-153, 200-206).

**Ryzyko:** ciche braki obrazów w dokumencie; trudne utrzymanie.
**Rekomendacja:** jawny komunikat gdy brak gridu; uproszczenie do jednego przebiegu.

---

### GF-024 — Dockerfile: podwójny `pip install .`, brak USER — ŚREDNIA [potwierdzone odczytem]

**Plik:** `Dockerfile:24-25,32`

Pakiet instalowany dwa razy; kontener działa jako root; `config.example.yaml` kopiowany jako `/app/config.yaml` (kontener z domyślnym configiem).

**Ryzyko:** dłuższy build, mylące warstwy; root w kontenerze to standardowy twardy anti-pattern bezpieczeństwa.
**Rekomendacja:** jeden install z cache warstw zależności, `USER` nie-root, config tylko przez mount/env.

---

### GF-025 — sys.exit(1) w bibliotece — ŚREDNIA [potwierdzone odczytem]

**Plik:** `vtd/cli.py:146-147`

Brak pliku wideo → `sys.exit(1)` w środku `run_pipeline`.

**Ryzyko:** nie można użyć pipeline jako biblioteki (worker/Studio odpala subprocess — działa, ale każdy inny integrator dostanie SystemExit zamiast wyjątku).
**Rekomendacja:** rzucać dedykowany wyjątek; exit tylko w warstwie CLI.

---

### GF-026 — Niespójny zapis PNG annotacji (dead branch, compress_level) — NISKA [potwierdzone odczytem]

**Plik:** `vtd/cli.py:527-534`

Save, potem `if not out_path.exists()` (martwe — plik właśnie zapisano), `compress_level=0` vs poziom 1 w gridzie.

**Ryzyko:** ogromne pliki PNG (level 0), myląca logika.
**Rekomendacja:** jeden poziom kompresji skonfigurowany, usunięcie martwej gałęzi.

---

### GF-027 — studio/templates: renderowanie niezweryfikowane — do weryfikacji [do weryfikacji]

Katalogi `vtd/studio/templates/` i `static/` **nie były odczytane w tej sesji**. Wiadomo, że podgląd instrukcji przechodzi przez bleach (GF-pozytywny), ale użycie filtrów `|safe` w szablonach nie zostało sprawdzone.

**Ryzyko:** potencjalny drugi kanał XSS obok GF-001 — niepotwierdzony.
**Rekomendacja:** audyt szablonów pod kątem `|safe` i autofescape.

---

### GF-028 — Stale scene_*.png / tmp w katalogach wynikowych — NISKA [potwierdzone odczytem]

`frame_extractor` nie czyta katalogu przed run; `image_enhancer.enhance_frame()` zostawia `_enh_raw.png` w `frames/`, a `frames/` jest kopiowany do client-ready.

**Ryzyko:** śmieci w artifacts dostarczanych klientowi; błędne liczby klatek przy rerun.
**Rekomendacja:** czyszczenie katalogów wyjściowych na starcie kroku.

---

### GF-029 — watchdog/env-loader duplikacja — NISKA [potwierdzone odczytem]

`watchdog.py` ma własny loader `.env` zduplikowany z `config._load_env_file`; trzy różne defaults enrich_model (GF-016) to symptom braku wspólnego modułu domyślnych.

**Rekomendacja:** jedna funkcja ładowania env, jedna tabela defaultów.

---

### GF-030 — CI: tylko testy, bez lint/type-check — NISKA [potwierdzone odczytem]

**Plik:** `.github/workflows/docker.yml:1-75`

CI uruchamia pytest + buildx (poprawnie: pinned actions, push tylko non-PR), ale brak ruff/mypy — stąd martwe gałęzie i literówki przetrwały.

**Rekomendacja:** dodać ruff + (opcjonalnie mypy) jako osobny job.

---

## 3. Pozytywy (co jest zrobione dobrze)

1. **Sparametryzowane SQL** w całym `db.py` (wartości przez `?`).
2. **Zero `shell=True`** — wszystkie subprocess budowane jako listy argumentów.
3. **Sanityzacja bleach** podglądu Studio (`sanitize.py:35-49`) + testy.
4. **Solidna anty-traversal** w `resolve_video_path` (`app.py:52-80`): `resolve()` + `is_relative_to` + test symlinka — z testami.
5. **Auth token + bindowanie do loopback** (`cli.py:1196-1202`, komentarz VTD-003 w Dockerfile).
6. **Timeouty ffmpeg skalowane czasem trwania mediów** (VTD-011, `audio_extractor.py:10-38`) — unika deadlocków na długich nagraniach.
7. **Recovery workera po restarcie** (VTD-005, `worker.py:72-93`) + timeout 4 h z grace terminate/kill (27-28, 96-112).
8. **Hierarchia źródeł klatek** enhanced→annotated→original (`frame_sources.py`) — przemyślane degradowanie.
9. **Pre-selekcja klatek ostrością** (Laplacian, `frame_sharpness.py`) — realna oszczędność LLM.
10. **Spójny manual-first DRAFT enforcement** — status FINAL tylko jawnie.
11. **Przemyślana precdencja env** (VTD-029, `_dotenv_injected`) z testami (`test_config.py:48-79`).
12. **Grupowanie re-review QA klatek** (MAX_REVIEWS batching) — ogranicza koszt LLM w normalnym przypadku.

---

## 4. Luki testowe

Stan: **35 testów, 35 passed, 3 warnings** (`uv run --extra dev python -m pytest tests/ -q`, 0.99 s).

Całkowity brak pokrycia (0 testów):
- `cli.run_pipeline` — najważniejsza funkcja projektu,
- `fusion.build_video_document` / gap-split,
- `frame_extractor`, `dedup`, `enricher`, `qa_verifier`, `rerender_docs`,
- `watchdog`, `image_enhancer` (775 LOC!),
- `worker._execute_task` i cały flow subprocess (timeout/kill/recovery poza testem konfiguracji),
- endpointy Studio: download, POST settings,
- `transcriber`, `audio_detection` (parsowanie wykryć na fiksaturach),
- edge-case'y `sanitize`.

Istniejące testy (studio auth/traversal/recovery, config precedence, annotator, openai_client, builders, frame_qa, element_locator) są dobre jakościowo — problemem jest **brak testów ścieżek krytycznych**, nie jakość obecnych.

---

## 5. Plan naprawczy (kolejność rekomendowana, bez implementacji w tej sesji)

1. **GF-001** — escapowanie HTML w html_builder + test regresyjny XSS. (godzina roboty, największy zysk bezpieczeństwa)
2. **GF-005** — pyyaml do zależności produkcyjnych + jawny warning przy braku configu.
3. **GF-002** — usunięcie magic hack 5200 z qa_verifier.
4. **GF-009 + GF-008 + GF-012** — zamiana cichych fallbacków (klucz/track/timestampy) na jawną obsługę błędów.
5. **GF-016** — jeden rejestr modeli/domyślnych dla CLI, Studio i settings.
6. **GF-006 + GF-007 + GF-026** — refactor run_pipeline: PipelineContext, usunięcie martwego kodu, oddzielne pole na wyniki QA.
7. **GF-003 + GF-004** — de-hardcodowanie watchdog i image_enhancer (konfiguracja + usunięcie martwych ścieżek).
8. **GF-014 + GF-015 + GF-017** — Studio: token w headerze, compare_digest, lifespan, streaming ZIP.
9. **Testy** — najpierw smoke-test `run_pipeline` na fiksaturach (audio+napisy+2 klatki), potem fusion i qa_verifier.
10. **GF-030** — ruff/mypy w CI, żeby martwy kod nie wracał.

---

## 6. Zakres analizy (pełna prawdziwość)

### Pliki odczytane w całości (z zakresami linii)

| Plik | Zakres |
|---|---|
| README.md | 1-243 |
| pyproject.toml | 1-59 |
| config.example.yaml | 1-42 |
| Dockerfile | 1-47 |
| docker-compose.yml | 1-26 |
| .github/workflows/docker.yml | 1-75 |
| vtd/__init__.py, vtd/__main__.py | całość (8 / 4 linie) |
| vtd/cli.py | 1-1213 (całość) |
| vtd/config.py | 1-248 |
| vtd/core/audio_extractor.py | 1-130 |
| vtd/core/transcriber.py | 1-50 |
| vtd/core/frame_extractor.py | 1-92 |
| vtd/core/dedup.py | 1-42 |
| vtd/core/frame_sources.py | 1-31 |
| vtd/core/frame_sharpness.py | 1-111 |
| vtd/core/fusion.py | 1-327 |
| vtd/core/audio_detection.py | 1-356 |
| vtd/core/annotator.py | 1-340 |
| vtd/core/element_locator.py | 1-196 |
| vtd/core/frame_qa.py | 1-145 |
| vtd/core/enricher.py | 1-184 |
| vtd/core/qa_verifier.py | 1-78 |
| vtd/core/watchdog.py | 1-237 |
| vtd/core/image_enhancer.py | 1-775 |
| vtd/core/rerender_docs.py | 1-281 |
| vtd/core/__init__.py | 1-50 |
| vtd/builders/manual_builder.py | 1-607 |
| vtd/builders/html_builder.py | 1-353 |
| vtd/providers/base.py, factory.py, openai_client.py, __init__.py | 1-40 / 1-18 / 1-212 / 1-5 |
| vtd/studio/app.py | 1-465 |
| vtd/studio/db.py | 1-168 |
| vtd/studio/worker.py | 1-292 |
| vtd/studio/settings_store.py | 1-70 |
| vtd/studio/sanitize.py | 1-49 |
| tests/conftest.py, test_studio, test_config, test_annotator, test_openai_client, test_builders, test_frame_qa, test_element_locator | całości (1-14, 1-194, 1-93, 1-74, 1-72, 1-46, 1-26, 1-26) |

Łącznie ~7 740 LOC Pythona (policzone `find vtd tests -name '*.py' | xargs wc -l`); największe: cli.py 1213, image_enhancer 775, manual_builder 607.

### Komendy uruchomione (z wynikami)

1. `uv run --extra dev python -m pytest tests/ -q` → **35 passed, 3 warnings in 0.99s** (warningi: FastAPI `on_event` deprecation).
2. `find vtd tests -name '*.py' | xargs wc -l` → inwentaryzacja LOC (7740 łącznie).
3. `ls .github/workflows/` → wyłącznie `docker.yml`.

### Czego NIE zrobiono (uczciwie)

- **Nie odczytano:** `vtd/studio/templates/**` i `vtd/studio/static/**` (renderowanie `|safe` — patrz GF-027), `uv.lock`, `LICENSE`, `config.example.json`, zawartości `data/`.
- **Nie uruchomiono:** ruff, mypy, żadnych narzędzi statycznych.
- **Nie testowano** pipeline end-to-end (żadnego nagrania, żadnego `vtd run`).
- **Nie użyto** istniejących raportów AUDYT_KODU.md / ANALIZA_KODU_GROK — zgodnie z regułami misji.
- Nie było **żadnych poprawek kodu** — sesja wyłącznie detektywistyczna; jedynym zapisanym plikiem jest niniejszy raport.

### Statystyka findings

| Waga | Liczba |
|---|---|
| KRYTYCZNA | 0 |
| WYSOKA | 7 (GF-001..GF-007) |
| ŚREDNIA | 13 |
| NISKA | 10 |
| **Razem** | **30** |

Brak wagi KRYTYCZNA: nie znaleziono błędu natychmiast destrukcyjnego (RCE, utrata danych, jawny wyciek sekretów do sieci). GF-001 (XSS) i GF-005 (cichy brak configu w prod) są najbliżej tej granicy.

---

*Koniec raportu. Wygenerowany wyłącznie na podstawie odczytów z sesji 2026-09-27.*
