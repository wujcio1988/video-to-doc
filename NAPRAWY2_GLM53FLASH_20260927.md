# NAPRAWY2_GLM53FLASH_20260927.md

Runda naprawcza #2 video-to-doc (vtd) — zakres zamknięty: DS-001, DS-002, DS-003, DS-004, DS-010 (+DS-028).
Przebieg: GLM-5.3-Flash (xkiro, opencode v2), 2026-09-27. Tylko realne uruchomienia, bez fabrykowania.

## Stan wyjściowy

- `git status --short` (punkt odniesienia): working tree z wcześniejszych fal VTD-*/GF-* niezacommitowany (28 plików zmodyfikowanych + raporty ANALIZA/AUDYT/NAPRAWY + 15 nowych plików testów, `vtd/defaults.py`, `vtd/studio/sanitize.py`, `uv.lock`).
- Baseline testów: `uv run --extra dev python -m pytest tests/ -q` → **114 passed, 1 warning in 1.87s**.
- Commitowanie: brak (zgodnie z regułami — NIE commitować).

## Wynik finalny

```
121 passed, 1 warning in 1.78s   (uv run --extra dev python -m pytest tests/ -q)
```

114 baseline + 7 nowych testów regresyjnych netto (DS-001 ×1, DS-002 ×1 — zastępuje stary test GF-003, DS-003 ×1, DS-004 ×1, DS-010 ×4). Poprzednich 114 testów nic nie zepsuło.

---

## DS-001 (KRYTYCZNY) — martwy `--annotate` (TypeError połykany)

**Status: NAPRAWIONE.**

- **Root cause:** `vtd/cli.py:506` i `vtd/cli.py:560` wywoływały `plan_annotations(..., llm_model="erp-code")`, a realna sygnatura w `vtd/core/element_locator.py:110-116` ma parametr `model: Optional[str] = None`. TypeError przy KAŻDYM wywołaniu był połykany przez szeroki `except Exception` w `vtd/cli.py:574-579` → adnotacje AI nigdy nie powstawały, pipeline raportował sukces.
- **Before:** `vtd/cli.py:506` — `planned = plan_annotations(s.step_number, title_for_llm, desc_for_llm, located, llm_model="erp-code")`
- **After:** `vtd/cli.py:509` — `planned = plan_annotations(s.step_number, title_for_llm, desc_for_llm, located)` (komentarz `cli.py:505-508` dokumentuje przyczynę). Drugi call-site: `vtd/cli.py:564` (grid frames) — identycznie bez martwego kwargsa.
- **Model z cfg zamiast twardego `erp-code`:** zrealizowane przez usunięcie kwargsa — `element_locator.py:132` (`chosen_model = model or cfg.llm.element_locator_model`) bierze model z konfiguracji, z override env przez `vtd/config.py:239`. Kod nie wymusza już żadnego modelu — konfiguracja wygrywa.
- **Test regresyjny:** `tests/test_cli_pipeline.py::test_ds001_regression_annotate_reaches_plan_annotations` (plik: ~linia 151-186). Realnie wywołuje `run_pipeline(..., annotate=True)` na mockowanym pipeline (fixture `mocked_env`) i przechodzi dokładnie tą ścieżką, gdzie siedział bug. Faker planowania mirroruje KANONICZNĄ sygnaturę `plan_annotations(step_number, step_title, step_description, located, model=None)` — ponowne wprowadzenie złego kwargsa (np. `llm_model=`) wywali test TypeError-em, tak jak w produkcji. Asercje:
  1. `plan_annotations` zostało faktycznie wywołane (wcześniej: TypeError przed wywołaniem → assert fail),
  2. kroki [1, 2] osiągnęły planowanie,
  3. cli nie narzuca modelu (`model=None` → decyduje cfg),
  4. brak komunikatu "Błąd globalny adnotacji" w stdout.

## DS-002 (REGRESJA z fali GF-003) — wysyłka Telegram

**Status: NAPRAWIONE.**

- **Root cause potwierdzony w kodzie:** `vtd/core/watchdog.py:91-98` — GF-003 budowało URL `https://api.telegram.org/bot/sendDocument` (bez tokenu w ścieżce) + nagłówek `Authorization: Bearer`. Telegram Bot API nie honoruje nagłówka Authorization (zweryfikowane na żywym API w analizie: `/bot/getMe` z Bearer → 404) → każda wysyłka DRAFT kończyła się 404.
- **Before:** `watchdog.py:92-98` — `curl -H "Authorization: Bearer <token>" ... https://api.telegram.org/bot/sendDocument`
- **After:** `watchdog.py:91-103` — `curl -F chat_id=... -F caption=... -F document=@... https://api.telegram.org/bot{bot_token}/sendDocument`. Mylący komentarz o nagłówku usunięty; nowy komentarz (`watchdog.py:91-97`) odnotowuje znany kompromis API: token w argv curl (widoczny lokalnie przez /proc) jest jedynym działającym kształtem wywołania Telegram Bot API — nie zamieniać z powrotem na nagłówek.
- **Druga funkcja wysyłki (`send_telegram_artifact`, `watchdog.py:115-130`):** w repo nie budowała żadnego URL — deleguje do zewnętrznego skryptu `~/.hermes/artifacts-server/scripts/hermes-send-artifact.sh`. Zweryfikowano statycznie: skrypt wysyła z tokenem w ścieżce (`bot${BOT_TOKEN}/sendMessage`) i bez nagłówka Authorization — kontrakt poprawny, nie wymagała naprawy; dodano odnotowanie tego w docstringu funkcji. Scope creep świadomie odrzucony (nie dodano nowej ścieżki wysyłki w repo).
- **Test regresyjny:** `tests/test_watchdog.py::test_ds002_regression_telegram_token_in_url_path` (zastępuje stary `test_gf003_regression_token_not_in_url`, który utrwalał ZŁY kontrakt). Asercje:
  1. w cmd jest dokładnie jeden URL i równy `https://api.telegram.org/botsupersecret/sendDocument` (token w ścieżce),
  2. żaden arg nie zaczyna się od `Authorization:` (Telegram nie obsługuje tego nagłówka).

## DS-003 — zaszyty model QA nadpisuje konfigurację

**Status: NAPRAWIONE.**

- **Root cause:** `vtd/cli.py:717` przekazywał `model="antigravity/gemini-3.8-flash-tiered"` do `audit_and_fix_manual(...)`, nadpisując `cfg.llm.qa_verifier_model`.
- **Before:** `cli.py:717` — `model="antigravity/gemini-3.8-flash-tiered"` w wywołaniu bramki QA.
- **After:** `vtd/cli.py:717-724` — wywołanie bez kwargsa `model` (komentarz DS-003). `qa_verifier.py:34` (`chosen_model = model or cfg.llm.qa_verifier_model`) wybiera wartość z konfiguracji z sensownym fallbackiem — identyfikator gatewaya usunięty z kodu (grep `antigravity` w `vtd/` → brak występowania).
- **Test regresyjny:** `tests/test_cli_pipeline.py::test_ds003_regression_qa_model_not_hardcoded_in_cli` (plik: ~linia 188-215). Spy na `vtd.core.qa_verifier.audit_and_fix_manual` + realne `run_pipeline(..., enrich=True)`; asercje:
  1. audyt faktycznie wywołany przez pipeline,
  2. cli nie przekazuje `model` (fallback na config realizuje `qa_verifier.py:34`),
  3. w źródle `vtd/cli.py` nie występuje identyfikator gatewaya.

## DS-004 (bezpieczeństwo) — `.env` wchodzi do obrazu Docker

**Status: NAPRAWIONE.**

- **Root cause:** `.dockerignore` nie wykluczał `.env`, a `Dockerfile:22` robi `COPY . .` → lokalny `.env` z kluczami trafia do warstwy obrazu.
- **Before:** `.dockerignore` — 17 linii, brak `.env`/`*.env`.
- **After:** `.dockerignore:18-21` — komentarz DS-004 + wpisy `.env`, `*.env`, `.env.*` (pełna rodzina plików z kluczami).
- **Test regresyjny:** `tests/test_docker.py::test_ds004_dockerignore_excludes_env` (nowy plik). Asercje: `.env` i `*.env` obecne jako samodzielne wpisy `.dockerignore`; brak negacji `!.env...` anulującej wykluczenie.

## DS-010 — auth Studia łamie przeglądarkę (statyki i formularze)

**Status: NAPRAWIONE (a: statyki — pełne; b: UI formularze — cookie-session po /login).**

- **Root cause:** middleware `vtd/studio/app.py:62-75` autoryzował wyłącznie nagłówkiem `X-VTD-Token`; przeglądarka nie dołącza nagłówka do `<link href="/static/...">`, `<img>` ani do natywnych `<form method="post">` (`new.html:8` → POST `/tasks`; `settings.html:5` → POST `/settings`) — wszystko dostawało 401 przy włączonym tokenie. JS fetch w szablonach także nie dostarcza nagłówka.
- **Before:** `app.py:33-34` — `AUTH_EXEMPT_PATHS = {"/health"}`; middleware 401-e wszystko poza `/health`.
- **After** (`vtd/studio/app.py:36-41`, `70-91`, `99-135`):
  1. **(a) Statyki:** `AUTH_STATIC_PREFIX = "/static/"` — wszystkie zasoby `/static/*` zwolnione z auth (niewrażliwe: `style.css`, `app.js`); `/health` i `/login` w `AUTH_EXEMPT_PATHS`.
  2. **(b) Formularze/UI:** sesja przeglądarki na cookie:
     - `GET /login` — ekran logowania (nowy `vtd/studio/templates/login.html`; zwolniony z auth — inaczej byłby chicken-and-egg; przy wyłączonym auth redirect na `/`);
     - `POST /login` — token w **treści formularza** (POST body, NIE query param → GF-014 pozostaje: `?token=` nadal odrzucane; body URL-encoded parsowane `urllib.parse.parse_qs`, zero nowych zależności). Zgodny token (porównanie stało-czasowe `_token_matches`) → `Set-Cookie: vtd_token=...; HttpOnly; SameSite=Strict; Path=/` (cookie sesyjne, bez max-age), redirect 303 na `/`.
     - Middleware akceptuje nagłówek LUB cookie (`app.py:79`) → natywne formularze (POST /tasks, /settings) oraz wszystkie fetch() w szablonach działają (fetch wysyła cookie same-origin domyślnie).
     - CSRF: SameSite=Strict nie dołącza cookie do żądań cross-site → formularz z obcej strony nie wykona akcji z cookie.
     - Nawigacja przeglądarkowa bez sesji: `GET` z `Accept: text/html` dostaje redirect 303 na `/login` (UX), natomiast API klienty (`Accept: */*`) mają deterministyczne 401 JSON — stary kontrakt nietknięty.
- **DS-028 (przy okazji, trywialne):** `.env.example:22-25` — blok `VTD_AUTH_TOKEN` przepisany: nie reklamuje już `?token=` (usuniętego z kodu, GF-014), opisuje nagłówek `X-VTD-Token`, cookie `vtd_token` po zalogowaniu na `/login` i wyraźnie stwierdza, że `?token=` NIE jest honorowany.
- **Testy regresyjne (`tests/test_studio.py`, plik: ~linia 137-186):**
  1. `test_ds010_static_exempt_when_token_set` — `/static/style.css` 200 przy włączonym tokenie;
  2. `test_ds010_login_grants_browser_cookie_session` — POST /login (token w body) → 303 + `vtd_token` w jarze; kolejne `GET /` 200 bez nagłówka;
  3. `test_ds010_login_wrong_token_rejected` — zły token → 401, brak cookie;
  4. `test_ds010_browser_html_navigation_redirects_to_login` — `Accept: text/html` bez sesji → 303 na `/login`; API (default Accept) nadal 401; `/login` otwarty.
  Istniejące testy auth (m.in. GF-014 `/?token=` → 401) niezmienione i przechodzą.

**Ryzyko (jasno):** endpoint `/login` jest otwarty gdy token wystawiony (standardowy ekran logowania); porównanie stało-czasowe (bez timing leak); cookie HttpOnly/SameSite=Strict ogranicza ekspozycję do kontekstu przeglądarki; brak rate-limitingu (akceptowalne dla narzędzia localhost/sieci lokalnej — ewentualne zaostrzenie zostawiono jako przyszłą decyzję, działa na BACKLOG).

## Podsumowanie zakresu (modyfikacje)

- `vtd/cli.py` — 3 fix call-site (DS-001 ×2, DS-003) + komentarze.
- `vtd/core/watchdog.py` — kontrakt URL wysyłki dokumentu (DS-002) + docstring drugiej ścieżki wysyłki.
- `vtd/studio/app.py` — middleware auth (cookie + zwolnienie statyków + redirect przeglądarkowy), `GET/POST /login`.
- `vtd/studio/templates/login.html` — NOWY (ekran logowania).
- `.dockerignore` — `.env`, `*.env`, `.env.*` (DS-004).
- `.env.example` — opis auth bez `?token=` (DS-028).
- `tests/test_watchdog.py` — stary test GF-003 zastąpiony poprawnym kontraktem DS-002.
- `tests/test_studio.py` — 4 nowe testy DS-010 + zaktualizowany komentarz testu GF-014.
- `tests/test_cli_pipeline.py` — 2 nowe testy (DS-001, DS-003).
- `tests/test_docker.py` — NOWY (DS-004).

**Nie ruszane:** rest working tree (wcześniejsze fale VTD-*/GF-*), raporty `AUDYT_KODU.md` / `ANALIZA_*.md` / `NAPRAWY_GLM53FLASH_20260927.md`, plik `.env`. Brak commitów/stash/resetu.

## Testy

```
$ uv run --extra dev python -m pytest tests/ -q
121 passed, 1 warning in 1.78s
```

Nowe testy regresyjne:
- `tests/test_cli_pipeline.py::test_ds001_regression_annotate_reaches_plan_annotations`
- `tests/test_cli_pipeline.py::test_ds003_regression_qa_model_not_hardcoded_in_cli`
- `tests/test_watchdog.py::test_ds002_regression_telegram_token_in_url_path`
- `tests/test_docker.py::test_ds004_dockerignore_excludes_env`
- `tests/test_studio.py::test_ds010_static_exempt_when_token_set`
- `tests/test_studio.py::test_ds010_login_grants_browser_cookie_session`
- `tests/test_studio.py::test_ds010_login_wrong_token_rejected`
- `tests/test_studio.py::test_ds010_browser_html_navigation_redirects_to_login`

Brak ODRZUCONE/BACKLOG — wszystkie pozycje zakresu (DS-001, DS-002, DS-003, DS-004, DS-010 + DS-028) NAPRAWIONE z testami regresyjnymi.
