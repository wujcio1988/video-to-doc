# Analiza rynkowa i produktowa — Video-to-Doc (VTD)

> **Typ dokumentu:** analiza strategiczna (nie kod). **Status:** DRAFT / DO WERYFIKACJI.
> **Data:** 2026-09-27. **Autor analizy:** deepseek-v4.1-flash.
> **Źródła:** brief rynkowy `oc-rynek-vtd-brief.md` (research web 2026-09-27) + inwentarz z audytów repo (`AUDYT_KODU.md`, `AUDYT_DOKUMENTACJI_VS_KOD_20260927.md`) + bezpośrednia inspekcja kodu `vtd/**` (2026-09-27).
> **Kontrakt:** wynik pipeline'u to zawsze DRAFT / DO WERYFIKACJI. Brak danych oznaczono jako „brak danych"; nie dopisywano faktów ani cen.

## 0. Wynik testów (weryfikacja realna)

Uruchomiono: `cd /home/kozlo/projects/video-to-doc && uv run --extra dev python -m pytest tests/ -q`

**Wynik: `135 passed, 1 warning in 1.88s`** (warning: deprecacja `starlette.testclient` z `httpx`).

Uwaga na kontekst liczbowy z briefu: brief mówi „135 (repo) + 227+31 (prod)". W tym repo faktycznie przechodzi **135** testów; zestawy 227/31 dotyczą środowiska produkcyjnego (`~/erp-workspace`), nie tego repo. Kod nie był zmieniany — to analiza.

---

## 1. Pozycjonowanie vs konkurencja

### 1.1 Gdzie VTD wygrywa (nisza)

| Przewaga VTD | Dlaczego konkurencja tego nie ma | Kto z konkurencji jest najbliżej |
|---|---|---|
| **Praca na GOTOWYM nagraniu** (OBS, archiwalne, spotkania) — bez capture na żywo | Scribe/Tango/iorad to capture-only (rozszerzenie przeglądarki, zdarzenia kliknięć). Nie przyjmują pliku wideo. | Odoo 19 AI Video Knowledge (najbliższy analog, ale zamknięty w Odoo) |
| **Fuzja 3+ sygnałów:** mowa (faster-whisper large-v3, pl) + zmiany scen (ffmpeg) + OCR (tesseract) + kursor | Guidde/Glitter robią wideo+głos, ale bez głębokiej semantyki OCR/scen; Scribe ma zdarzenia, ale brak mowy. | brak bezpośredniego (Guidde częściowo) |
| **Natywny DOCX (Word) z obrazami inline** dla branży wdrożeniowej PL | Konkurencja wychodzi do web/SaaS docs; brak .docx jako produktu dla klienta końcowego. | brak |
| **Local-first / offline**, brak zależności od chmury | Wszyscy bezpośredni konkurenci to SaaS/chmura. | Odoo (on-prem, ale tylko moduł Odoo) |
| **Metadane domenowe ERP** (klient/proces/moduł/środowisko/status DRAFT) + pipeline DRAFT→internal vs client-ready + Telegram delivery | To workflow wdrożeniowy, nie „generator guide'ów". | brak |
| **QA klatek (LLM) + adnotacje AI na zrzutach (element locator OCR)** + nano-banana enhance | Brief wskazuje to jako unikalne na rynku. | brak |
| **Tryb spotkań** (protokół: decyzje/działania/głosowania) obok trybu instrukcji | Capture-only narzędzia nie robią protokołów; Odoo Knowledge — nie. | brak |
| **Dojrzałość inżynierska** (135 testów repo, WAL, recovery kolejki, auth, SSRF/XSS hardening) jak na narzędzie wewnętrzne | Niszowe warianty (ProcessReel/Clevera/Vidocu/Docsie/Tella) — brak potwierdzonej dojrzałości. | — |

**Wniosek pozycjonujący (jedno zdanie):** VTD to **lokalny, offline-first konwerter dowolnego nagrania (w tym archiwalnego i spotkań) na weryfikowalną dokumentację ERP w Wordzie/HTML/MD — z audytowalnymi dowodami i adnotacjami AI** — czego żaden z bezpośrednich konkurentów SaaS nie oferuje.

### 1.2 Gdzie konkurencja miażdży

| Obszar | Kto wygrywa i dlaczego | Ocena dla VTD |
|---|---|---|
| **Capture na żywo z kliknięciami** — precyzja kroków na poziomie zdarzeń, zero szumu | Scribe, Tango, iorad | VTD nie ma i nie aspiruje; to inny model wejścia (gotowe wideo). Ryzyko: dla „szybkich SOP z przeglądarki" Scribe jest 10× szybszy. |
| **Analityka adopcji:** „gdzie użytkownicy się gubią", guided tours | Tango | VTD brak analityki i warstwy dystrybucji — bez tego nie konkuruje na rynku „product adoption". |
| **Voiceover wideo + wielojęzyczność z jednego nagrania** | Guidde, Glitter AI | VTD ma głos/transkrypcję wejściową, ale nie generuje lektora wideo wyjściowo. |
| **Dystrybucja / portal publikacji / współpraca zespołowa** | Scribe, Trainual, Guidde | VTD nie ma publikacji ani portalu. |
| **Time-to-value i UX** (rozszerzenie → gotowy SOP w minutach) | Scribe/Tango | VTD wymaga CLI/kolejki, konfiguracji, dłuższego pipeline'u. |
| **Onboarding/platforma SOP (zarządzanie procesami)** | Trainual | VTD to generator, nie platforma zarządzania wiedzą. |
| **Integracja z ekosystemem ERP** (tu: Odoo) | Odoo 19 AI Video Knowledge | Odoo wygrywa w Odoo; VTD przegrywa „w środku Odoo", ale wygrywa **poza** Odoo (Comarch/CTI/MWS — brief: Odoo tego nie obsługuje). |

**Konkluzja:** VTD nie powinien walczyć na polu „capture + guided tours + analytics + portal". Jego nisza to **ciężka, weryfikowalna dokumentacja powdrożeniowa ERP PL z gotowych nagrań** — i tu luki są w **edycji i wersjonowaniu własnego outputu**, nie w capture.

---

## 2. Macierz luk — TOP5 cech wg ROI (dla wdrożeniowca ERP PL)

Kryteria ROI: (a) czy zamyka lukę vs konkurencja, (b) czy wprost zwiększa dzienną produktywność wdrożeniowca, (c) koszt S/M/L, (d) zgodność z kontraktem DRAFT/local-first.

| # | Cecha z rynku | Skąd (wzorzec) | Wartość dla wdrożeniowca ERP PL | Koszt | ROI | Decyzja |
|---|---|---|---|---|---|---|
| 1 | **Edytor kroków in-app + re-render po edycji** | standard wszystkich SaaS (Scribe/Tango mają edycję SOP) | **Najwyższa** — dziś jedyna droga to ręczna edycja .md i CLI `rerender` (stratna rekonstrukcja). Bez tego output jest „szkicem, którego nie da się wygodnie dokończyć". | **M/L** | ★★★★★ | **ZROBIĆ (priorytet 1)** |
| 2 | **Kanoniczny model kroków (`steps.json`) + API** | fundament edycji/wersjonowania (brak wprost u konkurencji jako plik) | **Wysoka** — warunek konieczny #1 i #3; likwiduje stratną rekonstrukcję w `rerender`. | **S/M** | ★★★★★ | **ZROBIĆ (fundament)** |
| 3 | **Wersjonowanie i audyt zmian dokumentu** (kto/kiedy/diff, DRAFT-only) | standard dojrzałych platform (Trainual) | **Wysoka** — wdrożenia wymagają śladu zmian; dziś jest tylko `.bak.<ts>`. | **M** | ★★★★☆ | **ZROBIĆ (priorytet 2)** |
| 4 | **Publikacja/eksport dystrybucyjny** (portal link, pakiet per-klient, branding) | Scribe/Tango/Guidde | **Średnia** — DOCX już mamy (mocny); brakuje kontrolowanego kanału publikacji i szablonów. | **L** | ★★★☆☆ | **ZROBIĆ w scenariuszu produktowym (priorytet 3)** |
| 5 | **Szablony per-klient + rozszerzone metadata ERP + tryb internal/client-ready** | wyróżnik własny (już mamy zalążek) | **Średnia/wysoka** — tanie rozszerzenie istniejącego metadata; duża wartość brandingu i segregacji. | **S/M** | ★★★★☆ | **ZROBIĆ (priorytet 2, równolegle)** |

### Co świadomie POMINĄĆ (i dlaczego)

| Cecha | Kto ma | Powód pominięcia |
|---|---|---|
| Browser-capture (kliknięcia na żywo) | Scribe, Tango, iorad | Inny model wejścia; wysoki koszt (rozszerzenie + agent), kanibalizuje unikalną niszę „gotowe nagranie". Wrócić tylko przy pivocie na live-SOP. |
| Guided tours / in-app walkthrough | Tango | Wymaga wtyczki w aplikacji klienta (ERP on-prem) — poza kontrolą. |
| Analityka „gdzie user się gubią" | Tango | Wymaga telemetrii od użytkowników końcowych klienta — sprzeczne z local-first, brak kanału dystrybucji. |
| Voiceover wideo z lektorem AI | Guidde, Glitter | Poza niszą DOCX/Word; koszt L, konkurencja silna. |
| Publiczny SaaS multi-tenant | Scribe/Tango/Guidde/Trainual | Sprzeczne z local-first, offline i kontraktem DRAFT; dla ERP PL dane klienta nie mogą wyciekać. |
| Zamknięta integracja tylko z jednym ERP | Odoo | Ograniczałoby rynek (Comarch/CTI/MWS). Zamiast tego — neutralność + metadata. |

**Zasada:** nie kopiujemy przewag konkurencji w capture; kopiujemy **ich dojrzałość w edycji i wersjonowaniu**, bo tam VTD jest najsłabszy, a to dokładnie praca wdrożeniowca.

---

## 3. Scenariusz produktowy — narzędzie wewnętrzne vs produkt dla firm wdrożeniowych ERP (PL)

### 3.1 Dwa scenariusze

**Scenariusz A — narzędzie wewnętrzne (status quo+, rekomendowany start)**
- Dziś: CLI `run`/`rerender` + Studio lokalnie; produkcja przez `~/erp-workspace/tools/` (video-to-manual.sh + vtd_studio), watchdog OFF, DRAFT-only.
- Aby usprawnić pracę własnego zespołu (jedno biuro, WSL, jedna instancja), wystarczy:
  1. `steps.json` (kanoniczny model kroków),
  2. edytor kroków w Studio + re-render,
  3. proste wersjonowanie revisions.
- Zaleta: niski koszt, szybki ROI, zero zmian w modelu bezpieczeństwa. Brak: multi-tenant, RBAC, portal.

**Scenariusz B — produkt dla firm wdrożeniowych ERP (PL)**
- Odbiorca: inne firmy wdrożeniowe (Comarch/CTI/MWS/SAP B1), które chcą generować dokumentację klientom.
- Dodatkowo wymagane ponad A:
  - **multi-user + RBAC** (role: autor/weryfikator/publikujący) — DRAFT→weryfikacja→(nie FINAL automatycznie) jako workflow,
  - **portal publikacji / kontrolowany eksport per-klient** (link z tokenem, branding), 
  - **szablony i profil klienta** (branding DOCX/HTML, metadata wymuszone),
  - **instalator/dokumentacja wdrożeniowa** (on-prem; klienci ERP nie wpuszczą danych do chmury),
  - **licencjonowanie/wsparcie** — brak danych o modelu cenowym (nie zmyślamy).
- Koszt: znacząco większy; ryzyko: konkurencja z Odoo wewnątrz Odoo.

### 3.2 Rekomendowana ścieżka (ewolucyjna A → B)

Kolejność ma znaczenie — nie budujemy portalu, zanim dokumentu nie da się wygodnie **dokończyć**:

1. **Fundament (A):** `steps.json` + re-render z JSON + edytor w Studio. *(to domyka część 4)*
2. **Zaufanie (A/B):** wersjonowanie + diff + ślad autora (DRAFT-only).
3. **Dystrybucja (B):** szablony per-klient → portal/publikacja kontrolowana → RBAC.
4. **Produktyzacja (B):** instalator on-prem + dokumentacja + model licencji (do ustalenia — brak danych).

**Rekomendacja:** zacząć jako **narzędzie wewnętrzne z edytorem i wersjonowaniem** (Scenariusz A pkt 1–2). Dopiero po realnym użyciu przez zespół (feedback) decydować o Scenariuszu B. Edytor i wersjonowanie są wspólne dla obu — to najbardziej defensywna inwestycja.

---

## 4. „Edycja i tworzenie dokumentacji" — stan obecny + architektura (Studio vs CLI)

> Ta sekcja jest oparta na **bezpośredniej inspekcji kodu** (nie tylko briefu).

### 4.1 Co dziś REALNIE działa

**A. Tworzenie — CLI `vtd run` (vtd/cli.py, główna ścieżka ~1040–1309)**
- Wejście: dowolne wideo; tryb `manual` lub `meeting`; opcje `--enrich`, `--frame-qa`, `--annotate`, `--nano-banana`, `--output-mode`, metadata (`--client/--process/--module/--environment/--author/--document-status`).
- Etapy pipeline'u (z markerami postępu `##VTD_PROGRESS:{json}`): audio → silence → transcription → scenes → dedup → fusion → (frame_qa) → (annotations) → (enrich) → render.
- Zapisuje (line ~1154–1306): `INSTRUKCJA.md`, `INSTRUKCJA.html`, `INSTRUKCJA.docx` (python-docx, obrazy inline), `TRANSKRYPCJA.md`, `PROMPT_DLA_AGENTA.txt`, `COMPLETENESS_REPORT.md`, `metadata.json`, `manifest.json`, opcjonalnie `QA_AUDIT.md`, `ENRICHER_WARNING.md`; katalogi `frames/`, `frames_annotated/`, `frames_enhanced/`, `internal/`, `client-ready/`.
- Rendering: builders `render_markdown_manual` / `render_html_manual` / `render_docx_manual` przyjmują `steps: List[ManualStep]` + `enriched_data` + `metadata`.

**B. Re-render — CLI `vtd rerender <output_dir>` (vtd/core/rerender_docs.py + cli.py:75,1364)**
- Rekonstruuje listę kroków w kolejności: `PROMPT_DLA_AGENTA.txt` (markery `--- KROK`) → fallback `INSTRUKCJA.md` (nagłówki `### Krok N` + `Dowód`) → fallback skan `frames/`.
- Pisze **tylko** `INSTRUKCJA.md` i `INSTRUKCJA.html` (line 281–297), robi backup `.bak.<ts>` i aktualizuje też `client-ready/`.
- ⚠️ **Rozjazd dokumentacja↔kod:** README (linia 239) twierdzi, że `rerender` odtwarza „MD/HTML/DOCX" — w kodzie **nie ma generowania DOCX** w `rerender_output_dir`. To znany typ rozjazdu; warto zgłosić do audytu dokumentacji.

**C. Studio (vtd/studio/app.py, worker.py, db.py; templates)**
- Endpointy: `/`, `/new`, `POST /tasks`, `/tasks/{id}`, `/tasks/{id}/json`, `/tasks/{id}/log`, `/tasks/{id}/results`, `/tasks/{id}/download` (ZIP), `/settings`, `/api/track-detect`. **Brak jakiegokolwiek endpointu edycji/re-renderu.**
- Worker: uruchamia podproces `vtd run`, parsuje postęp, zapisuje log do `data/task_{id}.log`. DB (SQLite, WAL) ma **tylko** tabelę `tasks` — brak tabeli rewizji/kroków.
- `results.html`: renderuje `INSTRUKCJA.md`/`TRANSKRYPCJA.md`/`COMPLETENESS_REPORT.md` do HTML **read-only** przez `render_markdown_safe` (bleach, VTD-007), pokazuje metadata JSON i pliki client-ready + przycisk ZIP. **Brak edycji, brak podglądu DOCX, brak „przelicz ponownie".**

**D. Model danych**
- `ManualStep` (vtd/core/fusion.py) to **dataclass w pamięci** (step_number, start/end, frame_path, speech_text, grid_frames). `enriched_data` to **dict w pamięci** (tytuły/opisy/QA/annotacje), wpinany do renderów i promptu — **nie jest persystowany jako edytowalny plik**.
- Jedyny „plik prawdy" po runie to `metadata.json` (metadane, statystyki) — **brak kanonicznego pliku kroków**.

### 4.2 Czego brakuje do wygodnej edycji (luki twarde)

1. **Brak kanonicznego, edytowalnego modelu kroków.** `steps.json` nie istnieje → `rerender` **zgaduje** kroki z tekstu (stratne: fallback 0–5s, brak grid_frames, brak źródła/adnotacji outline).
2. **Brak edytora kroków.** Wdrożeniowiec nie może zmienić tytułu/opisu, przestawić kroku, podmienić klatki, usunąć kroku — musi edytować `.md` ręcznie.
3. **Brak re-renderu z UI.** `rerender` jest tylko CLI i nie jest podpięty do Studia; nie generuje DOCX (patrz 4.1B).
4. **Brak wersjonowania.** Tylko kopie `.bak.<ts>` (nieczytelny diff, brak autora, brak historii i przywracania).
5. **Brak „live preview" zmian** — edycja i render są rozdzielone, więc pętla feedbacku jest długa.
6. **DOCX tylko przy pełnym `run`** — po edycji .md nie da się odtworzyć spójnego DOCX bez pełnego re-runu pipeline'u (kosztownego).

### 4.3 Proponowana architektura: Studio = HITL, CLI = automatyzacja

**Zasada:** jeden wspólny rdzeń (builders + model kroków), dwa fronty:
- **CLI** — automatyzacja, batch, CI, produkcja (`~/erp-workspace`). Bez zmian semantyki: `run`, `rerender`.
- **Studio** — human-in-the-loop: edycja, podgląd, wersjonowanie, re-render, weryfikacja DRAFT.

**Krok 1 — kontrakt danych: `steps.json` (kanoniczny model)**
- Po `run` zapisać `steps.json` obok `metadata.json`: dla każdego kroku `{step_number, title, description, start, end, frame, grid_frames, annotations, annotation_legend, qa_preview, source}` + `enriched_data` scalone.
- Builders pozostają bez zmian (przyjmują `steps` + `enriched_data`); dodaje się **adapter** `steps.json → List[ManualStep] + enriched_data`.
- `rerender_output_dir` zmienia kolejność źródeł na: **`steps.json` → PROMPT → INSTRUKCJA.md → frames** (zachowanie wsteczne dla starych outputów). Dodać generowanie **DOCX** (usunięcie rozjazdu z README).

**Krok 2 — Studio: edytor kroków + re-render (nowe endpointy)**
- `GET /tasks/{id}/steps` → `steps.json` (+ listy dostępnych klatek z `frames/`, `frames_annotated/`, `frames_enhanced/`).
- `PUT /tasks/{id}/steps` → walidacja (timestamps, istnienie klatek), zapis nowej rewizji.
- `POST /tasks/{id}/rerender` → re-render MD/HTML/DOCX + (opcjonalnie) ZIP, bez re-runu Whisper/scen (szybko).
- Strona `editor.html`: lista kroków (drag&drop, edycja pól, wybór klatki), **live preview** HTML obok, przycisk „Zapisz i przelicz".
- Bezpieczeństwo: zachować `render_markdown_safe` (bleach), walidację ścieżek klatek wewnątrz output_dir, status zawsze DRAFT.

**Krok 3 — wersjonowanie**
- Katalog `revisions/` w output_dir: `rev_0001.steps.json`, `rev_0002.steps.json` + `revisions/index.json` `{rev, ts, author, note, hash_prev}`.
- Studio: historia, podgląd, przywrócenie, prosty diff pól. Nigdy nie usuwać oryginału; zachować DRAFT-only i manual-first.

**Wybór Studio vs CLI — jasne granice**
- Do **Studia** należą rzeczy interaktywne i wymagające kontekstu użytkownika: edycja, podgląd, wybór klatki, historia, akceptacja.
- Do **CLI** należy przetwarzanie ciężkie i wsadowe: transkrypcja, detekcja scen, QA, adnotacje, render w batchu, automatyzacja.
- **Nie duplikować logiki**: Studio zapisuje `steps.json` i woła ten sam `rerender` (import funkcji, nie shellowanie) — jedno źródło prawdy dla renderu.

**Uzasadnienie:** to najmniejszy zestaw zmian, który usuwa wszystkie 6 luk z 4.2 przy zachowaniu kontraktu DRAFT/local-first. `steps.json` jest fundamentem, który jednocześnie odblokowuje edycję (#1) i wersjonowanie (#3) i eliminuje stratność `rerender`.

---

## 5. Rekomendacje (cecha + uzasadnienie rynkowe + koszt S/M/L)

Kolejność = kolejność wdrażania. Koszty to **szacunki inżynierskie (S/M/L)**, nie wyceny — brak danych o stawkach.

| # | Rekomendacja | Uzasadnienie rynkowe | Koszt |
|---|---|---|---|
| R1 | **Kanoniczny `steps.json` + adapter do builders** (zapis po `run`, czytanie w `rerender`) | Fundament edycji i wersjonowania; likwiduje stratną rekonstrukcję („zgadywanie" kroków z MD/promptu). Bez tego każda dalsza cecha jest droższa i zawodna. Nie ma tego wprost u konkurencji jako otwarty plik — wspiera local-first. | **M** |
| R2 | **Edytor kroków w Studio + live preview + „Zapisz i przelicz"** (`GET/PUT steps`, `POST rerender`) | Standard rynkowy (Scribe/Tango/Guidde pozwalają edytować SOP). Zamyka lukę „brak edycji in-app" i sprawia, że wdrożeniowiec realnie kończy dokument bez ręcznej edycji .md. Najwyższy ROI. | **M/L** |
| R3 | **Re-render z DOCX + spójny eksport** (naprawa rozjazdu README↔kod) | DOCX to nasza przewaga vs SaaS; bez odtwarzalnego DOCX po edycji tracimy ją. Konsoliduje internal/client-ready. | **S** |
| R4 | **Wersjonowanie + diff + ślad autora** (`revisions/`, historia w Studio, DRAFT-only) | Wymóg procesowy wdrożeń ERP; wzorzec z platform (Trainual). Zastępuje `.bak.<ts>`. Buduje zaufanie do produktu (Scenariusz B). | **M** |
| R5 | **Szablony per-klient + rozszerzone metadata ERP + segregacja internal/client-ready** | Tanie rozszerzenie istniejącego metadata; wysoka wartość brandingu i zgodności z procesem klienta. Wzorzec: branding w Guidde/Trainual. Przygotowuje portal. | **S/M** |
| R6 | **Kontrolowana publikacja / portal link + RBAC** | Domknięcie dystrybucji (przewaga Scribe/Tango/Guidde). Wymagane dla Scenariusza B (produkt), nie dla wewnętrznego MVP. | **L** |
| R7 | **Instalator on-prem + dokumentacja wdrożeniowa** | Klienci ERP (Comarch/CTI/MWS) nie wpuszczą danych do chmury — local-first to warunek sprzedaży, ale wymaga pakietu instalacyjnego. | **L** |

**Świadomie odłożone (nie rekomendowane teraz):** browser-capture, guided tours, analityka „gdzie user się gubią", voiceover wideo, publiczny SaaS multi-tenant — patrz tabela „Co świadomie POMINĄĆ" w sekcji 2.

**Minimalny plan startowy (najlepszy stosunek wartości do kosztu):** **R1 → R3 → R2 → R4**. To sekwencja „fundament → spójny eksport → edycja → zaufanie", po której VTD jest kompletnym narzędziem wewnętrznym, a dopiero potem sensownie rozważać R5–R7 (produkt).

---

## 6. Ograniczenia analizy

- **Fakty rynkowe** pochodzą wyłącznie z briefu (research web 2026-09-27); nie dodawano nowych produktów ani cen. **Cenników konkurencji brak danych.**
- **Stan kodu** zweryfikowany inspekcją `vtd/**` i testami (135 passed). Liczby 227/31 z briefu dotyczą środowiska prod (`~/erp-workspace`), nie tego repo.
- **Koszty S/M/L** to szacunki złożoności, nie wyceny; brak danych o nakładach zespołu.
- Ustalenia o braku DOCX w `rerender` i braku endpointów edycji w Studiu wynikają z kodu; przed wdrożeniem potwierdzić testem integracyjnym (poza zakresem tej analizy — nie zmieniano kodu).
