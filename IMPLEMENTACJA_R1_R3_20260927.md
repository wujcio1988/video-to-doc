# Implementacja R1 + R3 — raport wykonania

**Data:** 2026-09-27
**Branch:** `fix/rundy-1-3` (HEAD `3418089` — bez commitów, bez zmiany brancha)
**Plan:** `PLAN_R1_R3_20260927.md` (załączony)
**Zakres:** R1 (`steps.json`) + R3 (DOCX w `vtd rerender`) + domknięcie README:239
**Model wykonawczy:** DeepSeek-v4.1-flash

---

## 0. Baseline i wynik końcowy suite

| | Komenda | Wynik |
|---|---|---|
| PRZED | `uv run --extra dev python -m pytest tests/ -q` | **135 passed**, 1 warning in 1.90s |
| PO | `uv run --extra dev python -m pytest tests/ -q` | **145 passed**, 1 warning in 1.88s |

Przyrost: **+10 nowych testów** (135 → 145), `0 failed`. Wszystkie bramki pakietów A–D przechodziły z pełnym suite zielonym.

Zmienione/nowe pliki (bez commitów):

```
 M README.md
 M tests/test_cli_pipeline.py
 M tests/test_rerender_docs.py
 M vtd/cli.py
 M vtd/core/rerender_docs.py
?? tests/test_steps_store.py   (nowy)
?? vtd/core/steps_store.py     (nowy)
```

`git diff --name-only` = wyłącznie 5 zmodyfikowanych plików z zakresu. Nie ruszano: `~/erp-workspace/**`, `.env`, `metadata.json` w istniejących outputach, generatorów treści poza zakresem.

---

## 1. Status per element planu (R1.x, R3.x) z `plik:linia`

### Pakiet A — `steps_store` (nowy moduł)

| Element | Status | `plik:linia` |
|---|---|---|
| A1 `save_steps_json` + `load_steps_json` + adaptery | ✔ | `vtd/core/steps_store.py:61` (`save_steps_json`), `:119` (`load_steps_json`), `:130` (`steps_json_to_manual_steps`), `:148` (`steps_json_to_enriched`) |
| Stałe | ✔ | `vtd/core/steps_store.py:20` (`STEPS_JSON_NAME = "steps.json"`), `:24` (`SECRET_MARKERS`) |
| Walidacja unikalności `step_number` | ✔ | `vtd/core/steps_store.py:74-79` (ValueError) |
| Walidacja braku sekretów | ✔ | `vtd/core/steps_store.py:37-45` (`_contains_secret`), `:105-111` |
| Ścieżki klatek względne | ✔ | `vtd/core/steps_store.py:28-36` (`_relative_frame`) |
| A2 testy T1+T2 | ✔ | `tests/test_steps_store.py:63` (T1), `:101` (T2), +` :111` (T7/AC10), `:123` (partial), `:132` (missing) |

### Pakiet B — rerender R1 (steps.json priorytetem)

| Element | Status | `plik:linia` |
|---|---|---|
| B1 importy steps_store | ✔ | `vtd/core/rerender_docs.py:26-30` |
| B2 blok `load_steps_json` przed rekonstrukcją | ✔ | `vtd/core/rerender_docs.py:242-261` (gałąź steps.json), `:263-277` (backward compat w `if not _from_steps_json`) |
| B3 testy T3+T4 | ✔ | `tests/test_rerender_docs.py:164` (T3), `:195` (T4) |

### Pakiet C — rerender R3 (DOCX)

| Element | Status | `plik:linia` |
|---|---|---|
| C1 import `render_docx_manual` | ✔ | `vtd/core/rerender_docs.py:24` |
| C2 blok DOCX + backup + client-ready | ✔ | `vtd/core/rerender_docs.py:322-345`; klucz `docx` w wyniku `:347` |
| C3 testy T5+T6 | ✔ | `tests/test_rerender_docs.py:218` (T5), `:241` (T6) |

### Pakiet D — cli.py (zapis po `vtd run`)

| Element | Status | `plik:linia` |
|---|---|---|
| D1 import `save_steps_json` | ✔ | `vtd/cli.py:15` |
| D2 wywołanie po `render_html_manual` | ✔ | `vtd/cli.py:1174-1184` |
| D3 guard `enriched_data is None` → zapis częściowy | ✔ | wewnątrz `save_steps_json` (`vtd/core/steps_store.py:99` → `enriched_data: null`); brak wyjątku |
| D3b guard `existing_ann_map` (R8) | ✔ | `vtd/cli.py:1131` (`existing_ann_map: Dict[int, Any] = {}` przed blokiem adnotacji) |
| D4 test asercji steps.json | ✔ | `tests/test_cli_pipeline.py:78` (`test_r1_steps_json_saved_after_run`) + wpis `"steps.json"` w smoke `:67` |

### Pakiet E — README

| Element | Status | `plik:linia` |
|---|---|---|
| E1/E2 domknięcie rozjazdu linii 239 | ✔ | `README.md:239` — źródło zmienione na „prefers `steps.json` … falls back to `metadata.json` / `PROMPT_DLA_AGENTA.txt` / `INSTRUKCJA.md`” |

---

## 2. Nowe testy + realny output pytest

### Nowe testy (10)

| ID | Test | Plik:linia | Co dowodzi |
|---|---|---|---|
| T1 | `test_steps_store_save_and_load_roundtrip` | `tests/test_steps_store.py:63` | round-trip save→load bez utraty kroków/dat + względny `frame_path` |
| T2 | `test_steps_store_duplicate_step_number_raises` | `tests/test_steps_store.py:101` | duplikat `step_number` → `ValueError` (AC8) |
| T7* | `test_steps_store_rejects_secrets` | `tests/test_steps_store.py:111` | marker sekretu → `ValueError`, plik nie powstaje (AC10) |
| T1b* | `test_steps_store_enriched_none_is_null` | `tests/test_steps_store.py:123` | zapis częściowy `enriched_data: null` (R1 ryzyko) |
| T1c* | `test_steps_store_load_missing_returns_none` | `tests/test_steps_store.py:132` | brak pliku → `None` (backward compat) |
| T3 | `test_rerender_uses_steps_json_when_present` | `tests/test_rerender_docs.py:164` | rerender bierze kroki z `steps.json`, ignoruje MD-pułapkę (AC4) |
| T4 | `test_rerender_fallback_without_steps_json` | `tests/test_rerender_docs.py:195` | brak `steps.json` → rekonstrukcja z PROMPT, brak wyjątku (AC5) |
| T5 | `test_rerender_generates_docx` | `tests/test_rerender_docs.py:218` | `render_docx_manual` raz + klucz `docx` + kopia client-ready (AC6) |
| T6 | `test_rerender_docx_backup_created_if_exists` | `tests/test_rerender_docs.py:241` | glob `INSTRUKCJA.bak.*.docx` == 1 (AC7) |
| AC2 | `test_r1_steps_json_saved_after_run` | `tests/test_cli_pipeline.py:78` | po `run_pipeline` istnieje `steps.json` (AC2) |

\* T7 oraz T1b/T1c to dodatki ponad T1–T6: T7 domyka AC10 (plan nie miał osobnego ID testu), a T1b/T1c domykają wprost ryzyko R1 i kontrakt backward compat.

### Realny output pytest PRZED / PO

```
# PRZED (baseline, potwierdzony na starcie):
135 passed, 1 warning in 1.90s

# PO (po pakietach A–E):
145 passed, 1 warning in 1.88s
```

Wybrane zielone przebiegi bramek:
```
Gate A: tests/test_steps_store.py → 5 passed;        pełny suite → 140 passed
Gate B: tests/test_rerender_docs.py → 8 passed;      pełny suite → 142 passed
Gate C: tests/test_rerender_docs.py → 10 passed;     pełny suite → 144 passed
Gate D: tests/test_cli_pipeline.py → 11 passed;      pełny suite → 145 passed
```

---

## 3. Dowód kryteriów akceptacji (sekcja 5 planu)

| ID | Kryterium | ✔/✘ | Jak sprawdzone (realnie) |
|---|---|---|---|
| AC1 | suite ≥ 135 passed, 0 failed | ✔ | `145 passed, 1 warning` — final run |
| AC2 | `steps.json` zapisywany po `vtd run` | ✔ | `tests/test_cli_pipeline.py:78` (`(out/"steps.json").exists()` + treść); smoke `:67` |
| AC3 | round-trip bez utraty kroków/dat | ✔ | T1: `step_number/start_time/end_time/speech_text` + `frame_path`; dodatkowo realny E2E `save_steps_json → rerender_output_dir` (steps=1, tytuł z JSON w MD) |
| AC4 | rerender używa steps.json, ignoruje MD | ✔ | T3: spy na `render_markdown_manual` — kroki `[1,2]`, t=(3.0,9.0), tytuł „Tytuł kroku z JSON”; pułapka „TYTUŁ Z MD — PUŁAPKA” nieobecna. Realny E2E: MD zawiera „Tytul z JSON ALFA” (2×), 0× pułapki |
| AC5 | rerender bez steps.json działa jak dziś | ✔ | T4 (parser `_timestamps_from_prompt` wywołany, t=(12.0,25.5)); realny E2E na katalogu bez `steps.json` — brak wyjątku, MD+DOCX wygenerowane, DOCX poprawnym zipem |
| AC6 | rerender generuje `INSTRUKCJA.docx` | ✔ | T5 (`counter==1`, klucz `docx`, kopia w client-ready); realny E2E: `INSTRUKCJA.docx` PK/zip, `zipfile.is_zipfile == True` |
| AC7 | backup `.bak.<ts>.docx` przy rerenderze | ✔ | T6 (glob == 1, stara treść zachowana); realny E2E po 2. uruchomieniu: 1× `INSTRUKCJA.bak.20260927_204628.docx` |
| AC8 | duplikat `step_number` → wyjątek | ✔ | T2 `pytest.raises(ValueError)` |
| AC9 | README:239 spójny z kodem | ✔ | `git diff README.md` — 1 linia, opisuje steps.json + fallback; brak innych wzmianek `metadata.json`/`steps.json` w README |
| AC10 | brak sekretów w steps.json | ✔ | `_contains_secret` + T7; realny E2E: `{"intro":"Bearer XYZ"}` → `ValueError` (plik nie powstał) |

---

## 4. Odchylenia od planu + uzasadnienie

Wszystkie są drobnymi doprecyzowaniami luk w pseudokodzie — bez zmian architektury ani sygnatur builderów. Żadnego elementu nie pominięto.

1. **`annotation_legend=annotation_legend` (plan 2.1) → `annotation_legend=None` (cli).**
   W `cli.py` nie istnieje pojedyncza zmienna `annotation_legend` — legendy są per-krok wewnątrz `enriched_data["steps"][].annotation_legend`, które i tak zapisujemy w surowym `enriched_data`. Pole najwyższego poziomu `annotation_legend` pozostaje `null` (schemat: opcjonalne). Nie zmyślono nowej struktury.

2. **Backup DOCX: `.rename()` (plan 2.3) → `shutil.copy2` z tą samą nazwą `INSTRUKCJA.bak.<ts>.docx`.**
   Zachowanie obserwowalne identyczne (glob AC7 spełniony), a `copy2` jest spójne z istniejącym `_backup_file` dla MD/HTML i chroni oryginał, gdyby `python-docx` był niedostępny (builder przy `ImportError` zwraca ścieżkę bez zapisu — `rename` zgubiłby plik).

3. **steps.json używane tylko gdy zawiera kroki.**
   Plan: „if `_data is not None`”. Przy pliku istniejącym, ale z pustą listą kroków, rerender schodzi do starej rekonstrukcji (`if not _from_steps_json`), zamiast wygenerować puste dokumenty. To zabezpieczenie przed korupcją/przypadkowym pustym zapisem.

4. **Adapter `steps_json_to_manual_steps(data)` bez `output_dir`.**
   Sygnatura wg planu (bez `output_dir`), a schemat mówi, że `frame_path` jest względem `output_dir`. Rozwiązanie: adapter zwraca `Path` tak, jak zapisano (względny), a `rerender_output_dir` odbudowuje ścieżki absolutne względem `output_dir` przed builderami (`vtd/core/rerender_docs.py:250-257`) — realizacja ryzyka R2.

5. **`load_steps_json` zwraca `None` także dla uszkodzonego JSON** (nie tylko braku pliku).
   Defensywne, spójne z istniejącym `_load_metadata` (łapie `Exception`); uszkodzony plik → fallback zamiast twardego crashu rerendera.

6. **`intro` dla DOCX wyliczony z `enriched_data["intro"]`.**
   Pseudokod planu miał `intro=intro`, ale w `rerender_output_dir` nie ma zmiennej `intro` (buildery i tak nadpisują je `enriched_data["intro"]` — `manual_builder.py:434-435`). Przekazana wartość jest więc spójna i niezmienna.

7. **Dodatkowy test T7 (AC10).**
   Plan nie przewidział ID testu dla AC10 — dodano `test_steps_store_rejects_secrets`, by kryterium miało realny, wykonywalny dowód.

8. **Marginalne:** blok zapisu steps.json wstawiony po `render_html_manual` (a nie po `render_docx_manual`) — obie wstawki są po obu renderach, kolejność bez znaczenia funkcjonalnego; komentarz planu sam wspomina „po linii ~1168 (po render_html_manual)”.

**Luka/błąd blokujący w planie:** brak. Plan był wykonalny w całości.

---

## 5. Sekcja prawdy — co realnie działa po przebiegu

Weryfikacja **realnymi uruchomieniami** (nie tylko mocki), na katalogach w `/home/kozlo/.hermes/cache/scratch/opencode/`:

1. **`vtd rerender` z `steps.json`** (realne buildery, `INSTRUKCJA.md/.html/.docx`):
   - MD zawiera tytuły z `steps.json` („Tytul z JSON ALFA” ×2), **nie zawiera** tytułu-pułapki z pliku MD;
   - `INSTRUKCJA.docx` to poprawny plik Word (`PK` magic, `zipfile.is_zipfile == True`, `python-docx 1.2.0`);
   - drugie uruchomienie tworzy dokładnie `INSTRUKCJA.bak.20260927_204628.docx` (stara treść zachowana, nowa nadpisuje);
   - kopia DOCX do `client-ready/` gdy katalog istnieje (T5).

2. **Backward compat bez `steps.json`:** katalog z `metadata.json` + `PROMPT_DLA_AGENTA.txt` + `INSTRUKCJA.md` → rerender bez wyjątku, rekonstrukcja z PROMPT (timestampy z promptu, nie 0–5 s), DOCX wygenerowany poprawnie. Istniejący test `test_rerender_full_flow_backup_and_rewrite` nadal zielony.

3. **Integracja `save_steps_json` → `rerender`:** realny zapis (ManualStep + enriched) → rerender → `steps=1`, klucz `docx`, tytuł z JSON w MD.

4. **AC10 realnie:** próba zapisu z `"Bearer XYZ"` w `intro` → `ValueError`, `steps.json` nie powstaje.

5. **`vtd run` zapisuje `steps.json`:** potwierdzone testem integracyjnym na mockach pipeline (`test_r1_steps_json_saved_after_run`: `version=1.0`, kroki `[1,2]`, `enriched_data=null` bez `--enrich`, ścieżki względne).

### Ograniczenia / czego NIE wykonano

- **Nie uruchomiono realnego `vtd run` na nagraniu** (wymaga ffmpeg + Whisper + pliku wejściowego; nie było nagrania w zakresie). Ścieżka `run_pipeline` jest pokryta testem integracyjnym z mockami zależności zewnętrznych, który tworzy **prawdziwy** `steps.json` na dysku.
- **`steps.json` nie jest kopiowany do `client-ready/` ani dopisywany do `manifest.json`** — plan tego nie wymagał (traktowany jako wewnętrzny snapshot kanoniczny obok `metadata.json`).
- **Brak commitów** zgodnie z regułami; zmiany pozostają w working tree na `fix/rundy-1-3`.
- Potencjalny efekt uboczny: istniejące testy `rerender` z fikcyjnym PNG (bajty `b"png"`) przechodzą, bo osadzanie obrazów w DOCX jest w `try/except` i poprzedzone `frame_for_doc.exists()`.
