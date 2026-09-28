# RUNDA NAPRAWCZA: enrich LLM — raport zamknięty

- **Data:** 2026-09-27
- **Repo:** `/home/kozlo/projects/video-to-doc`
- **Branch:** `fix/rundy-1-3` (bez commitów, bez zmian brancha)
- **Zakres:** R-A .. R-E implementacja, R-F testy regresyjne. Nic poza zakresem.

---

## 1. Status per R-A .. R-E (plik:linia PO zmianie)

| ID | Status | Zmiana | Lokalizacja |
|----|--------|--------|-------------|
| **R-A** | ✅ ZROBIONE | `_execute_request`: gdy `content` pusty, próba odzyskania z `reasoning_content`; w razie braku parsowalnego JSON → jawny `ValueError` (nigdy ciche `""`). Dodany helper `_recover_content_from_reasoning`. | `vtd/providers/openai_client.py:130` (odzysk), `:139` (jawny błąd), `:166` (helper) |
| **R-B** | ✅ ZROBIONE | `robust_json_decode`: pusta/whitespace treść → `ValueError "Pusta odpowiedź od LLM..."` (koniec cichego `{}`); nieparsowalna → `ValueError` z pierwszymi 200 znakami. Enricher loguje rozróżnienie. | `vtd/providers/openai_client.py:21,32`; `vtd/core/enricher.py:215` (pusty content), `:221` (błąd parsowania + treść) |
| **R-C** | ✅ ZROBIONE | `max_tokens=2500` → `8000`. | `vtd/core/enricher.py:208` |
| **R-D** | ✅ ZROBIONE | Dokładnie 1 automatyczny retry przy `None`/pustym wyniku, ta sama prośba, log ostrzeżenia. Realizacja: `_attempt()` + jedna ponowna próba. | `vtd/core/enricher.py:201` (`_attempt`), `:227` (log retry), `:230` (finalny log) |
| **R-E** | ⚠️ ZROBIONE częściowo | Domyślny `LLMConfig().enricher_model`: `"gpt-4o-mini"` → `"opencode-go/deepseek-v4.1-flash"`. Flaga `--enrich-model` bez zmian (nadal nadrzędna). **Uwaga — patrz sekcja 5 (luka G-1):** ścieżka CLI nadal nadpisuje ten default. | `vtd/config.py:24` |

## 2. Testy (R-F) — `tests/test_enrich_naprawa.py` (nowy plik, 7 testów)

| Wymóg R-F | Test | Linia |
|-----------|------|-------|
| (1) pusty content + `reasoning_content` z JSON → odzysk | `test_r_a_recovers_json_from_reasoning_content` | `:36` |
| (1b) pusty content bez reasoning → jawny błąd | `test_r_a_empty_content_no_reasoning_raises_explicit` | `:48` |
| (2) pusta treść → nie ciche `{}` | `test_r_b_empty_text_raises_not_silent_empty_dict` | `:60` |
| (2) nieparsowalna → jawny log z fragmentem | `test_r_b_unparsable_includes_excerpt_and_enricher_logs` | `:67` |
| (3) retry przy pierwszym None | `test_r_d_retries_once_on_first_none` | `:95` |
| (4) `max_tokens == 8000` | `test_r_c_enricher_max_tokens_is_8000` | `:122` |
| (4) domyślny `enricher_model` z R-E | `test_r_e_default_enricher_model` | `:141` |

## 3. Realny output pytest PRZED / PO (liczby)

**PRZED (baseline, przed jakąkolwiek zmianą):**
```
145 passed, 1 warning in 2.01s
```

**PO (po R-A..R-F, cały suite `tests/`):**
```
152 passed, 1 warning in 2.25s
```
Cel `≥149 passed, 0 failed` — osiągnięty (145 → 152, +7 nowych, 0 failed).

Zmiana liczby: baseline 145 + 7 nowych testów = 152. Żaden istniejący test nie został zmodyfikowany ani usunięty.

## 4. Sekcja prawdy — co naprawa zmienia w zachowaniu realnym

### Scenariusz: model zużywa budżet na reasoning i zwraca `content == ""`

**KIEDYŚ (przed naprawą):**
1. `_execute_request` robił `return content or ""` → puste `""` bez śladu.
2. `robust_json_decode("")` → cicho `{}` (linia `if not text: return {}`).
3. Enricher zwracał `{}`; w `_stage_enrich` `if enriched_data:` jest fałszywe dla `{}` → komunikat „**wzbogacanie LLM zwróciło None — draft bez enrich**" **bez przyczyny**. Zero informacji, że problem to pusty `content` / reasoning.

**TERAZ (po naprawie):**
1. `_execute_request` widzi pusty `content`, próbuje odzyskać pierwszy obiekt JSON z `reasoning_content` (cała treść JSON → blok `{...}` → blok `[...]`).
   - Gdy się uda: loguje `[OSTRZEŻENIE] ... odzyskano treść z 'reasoning_content' (N znaków)` i zwraca odzyskany JSON → enricher go parsuje → **sukces zamiast None**.
   - Gdy nie ma parsowalnego JSON: **jawny `ValueError`** z fragmentem reasoning (nigdy ciche `""`).
2. Gdy enricher mimo to dostanie `None`/pusty/pusty dict — wykonuje **1 retry** tej samej prośby, logując ostrzeżenie; dopiero po drugim niepowodzeniu zwraca `None` (i wtedy `_stage_enrich` zapisuje `ENRICHER_WARNING.md`, jak dotychczas).
3. Pusta lub nieparsowalna treść zawsze pozostawia ślad w logu: `pusty content` albo `błąd parsowania JSON, treść: <200 znaków>`.

**Dodatkowo (behavior change, pozytywny):** `robust_json_decode` przestał zwracać `{}` dla pustego wejścia — teraz rzuca `ValueError`. Wszyscy pozostali wołający (`qa_verifier.py`, `element_locator.py`, `frame_qa.py`) mają już własne `try/except Exception`, więc zamiast iść dalej na `{}` zgłoszą jawny komunikat przyczyny w swoich ścieżkach fallback. Tolerancyjne wyciąganie bloków ```json / klamr dla **niepustych** danych pozostało nietknięte (istniejące testy przechodzą).

**Zasięg R-A:** odzysk/jawny błąd zaimplementowano w `_execute_request` (wspólny parser odpowiedzi), więc obejmuje także `vision_completion`. To celowe: pusty `content` z reasoningiem to problem warstwy transportu, nie pojedynczej metody. Publiczne sygnatury bez zmian.

## 5. Odchylenia / luki (wprost)

### G-1 (istotne): R-E na ścieżce CLI jest nadpisywane — domyślne uruchomienie CLI NADAL wyśle `gpt-4o-mini`
Zgodnie z zakresem zmieniono **tylko** `vtd/config.py:24`. Jednak domyślne `vtd run --enrich` **nie korzysta** z tego defaultu, bo:
- `vtd/cli.py:53` — argparse `--enrich-model` ma `default=DEFAULT_ENRICH_MODEL`,
- `vtd/defaults.py:10` — `DEFAULT_ENRICH_MODEL = "gpt-4o-mini"`,
- `vtd/cli.py:772` — `run_pipeline(..., enrich_model: str = DEFAULT_ENRICH_MODEL)`,
- enricher: `chosen_model = model or app_cfg.llm.enricher_model` (`vtd/core/enricher.py:107`) — ponieważ CLI zawsze przekazuje niepuste `model`, `app_cfg.llm.enricher_model` (zmienione na R-E) **nie jest używane**.

Dodatkowo `.env` z `OPENAI_MODEL=...` (`vtd/config.py:239-242`) nadpisuje wszystkie pola modeli.

**Skutek:** cel R-E („żeby domyślne uruchomienie nie trafiało do openrouter 402") **nie jest w pełni osiągnięty** samą zmianą `config.py`. Aby domyślny CLI faktycznie używał nowego modelu, trzeba by dodatkowo zmienić `DEFAULT_ENRICH_MODEL` w `vtd/defaults.py:10` **oraz** dodać `"opencode-go/deepseek-v4.1-flash"` do `DEFAULT_MODELS`/`ALLOWED_MODELS` w `vtd/studio/settings_store.py:17`, inaczej walidacja Studio (GF-016) cofnęłaby się do modelu spoza listy. **Nie zrobiono tego — świadomie poza zakresem „dokładnie to, nic więcej".** Flagą `--enrich-model opencode-go/deepseek-v4.1-flash` (lub `OPENAI_MODEL` w env) można wymusić model już teraz.

Test R-E (`test_r_e_default_enricher_model`) potwierdza wyłącznie wartość `LLMConfig().enricher_model`, zgodnie z treścią R-E/R-F.

### G-2 (kosmetyczne): komentarz w `testing`/prompt
Brak. Nie dotykano opisów metadanych ani innych domyślnych modeli (`frame_qa_model`, `element_locator_model`, `qa_verifier_model`) — poza zakresem.

## 6. Zgodność z regułami
- Publiczne sygnatury bez zmian; brak refaktorów pobocznych.
- Pełny suite zielony: **152 passed, 0 failed** (baseline 145).
- Nie ruszano: `~/erp-workspace/**`, `.env`, katalogów wynikowych, `steps_store.py`, `rerender_docs.py`.
- Bez sekretów w kodzie/testach/raporcie. Bez commitów.
- `git status`: zmodyfikowane `vtd/config.py`, `vtd/core/enricher.py`, `vtd/providers/openai_client.py`; nowy `tests/test_enrich_naprawa.py`. Branch `fix/rundy-1-3` bez zmian.

---

## 7. Rozliczenie końcowe (weryfikacja niezależna, 2026-09-28)

Po przebiegu naprawczym dokończono diagnostykę realnymi przebiegami na nagraniu
`~/erp-workspace/recordings/2026-09-18 08-50-19.mkv` (4,7 min):

- **Luka G-1 domknięta:** skuteczny default to `vtd/defaults.py` (`DEFAULT_ENRICH_MODEL`),
  nie `config.py`. Ujednolicono oba źródła i wzmocniono test (assert na oba).
- **Timeout klienta:** `60 s` → `180 s` (`vtd/providers/openai_client.py`, `vtd/config.py`)
  — realny czas odpowiedzi wzbogacania bywał powyżej 60 s.
- **Ścieżka doboru modelu enrich (realne próby):**
  - `gpt-4o-mini` (stary default) → routowany do openrouter: 402 (brak kredytów) / 429 (cooldown)
  - `opencode-go/deepseek-v4.1-flash` → pusty `content` (budżet na `reasoning_content`) — naprawiony przez R-A, ale zgodnie z decyzją użytkownika enrich ma używać innego modelu
  - `opencode-go/glm-5.3-flash` → 504 z OmniRoute: sufit `requestQueue.maxWaitMs=45000 ms` (model ~40 s samodzielnie, powyżej 45 s przy cięższym prompcie)
  - **`opencode-go/minimax-m3` → OK (9,8 s standalone)** — przyjęty jako default enrich
- **Werdykt na realnym filmie:** `ENRICH: OK` — meta realne z OCR ekranów (proces
  „Definicja technologi TH/00006/2026", moduł „Produkcja by FO"), intro wygenerowane,
  10/10 kroków wzbogaconych, brak `ENRICHER_WARNING.md`, 15 artefaktów, suite 152 passed.
- **Pozostałość (do decyzji):** pomocniczy audyt QA / frame QA / element locator mają
  nadal default `gpt-4o-mini` (429 z openrouter) → są pomijane z ostrzeżeniem
  (nieblokujące). Przełączenie na szybkie modele = osobna decyzja.
