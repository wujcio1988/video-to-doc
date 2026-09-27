# RUNDA 3 — raport GLM-5.3-Flash (2026-09-27)

Repo: `/home/kozlo/projects/video-to-doc` · Model: GLM-5.3-Flash · Baseline po Runda 2: **121 passed**, git `11c2364/566b6ba/1c3d739`.

## Zakres

Faza 1 (kod) — naprawy: **VTD-012, VTD-014, VTD-018, VTD-021, VTD-023, VTD-027, VTD-028** + 14 nowych testów.
Faza 2 (docs) — sync README/base.html/config.example/SKILL.md.
Faza 3 — 2 commity (bez push).

## Faza 1 — zmiany w kodzie

1. **VTD-021 (numpy poza deps):** `numpy>=1.24.0` dodane do `project.dependencies` w `pyproject.toml`; `uv lock` zregenerowany.
2. **VTD-027 (retry/jitter w openai_client):** retry tylko dla przejściowych błędów (`HTTPError` 429/5xx, `URLError`, `ConnectionError`, `TimeoutError`, `OSError`); `ValueError` (brak choices) bez retry — natychmiastowy fail. Nowy `_backoff_seconds(attempt)`: min(2^attempt, 32 s) × jitter `random.Random(time.monotonic_ns()+attempt).uniform(0.75–1.25)`.
3. **VTD-014 (SQLite konkurencja w db.py):** `PRAGMA journal_mode=WAL`, `busy_timeout=5000`, `synchronous=NORMAL` w `get_connection`; `init_db` wykonywany raz na ścieżkę (guard `_initialized_paths`).
4. **VTD-012 (progress w workerze):** parser `##VTD_PROGRESS:{json}` (priorytet) + fallback na polskie stringi `Krok N/6`; zapis `stage`/`progress_note` przez `update_task`; mapowanie `_STAGE_NOTES` oraz emitowanie markerów w CLI.
5. **VTD-018 (refaktoryzacja run_pipeline):** pipeline rozbity na stage helpery: `_stage_audio_and_transcribe`, `_stage_extract_unique_frames`, `_stage_fuse_steps`, `_stage_frame_qa`, `_stage_annotations`, `_stage_nano`, `_stage_enrich`, `_emit_progress`. Sygnatura `run_pipeline` **bez zmian** (weryfikowana testem AST — 25 parametrów łącznie z self-inspection tail `frame_qa … nano_auto_accept`).
6. **VTD-023 (pipeline w cli.py):** PipelineConfig budowany **jednokrotnie** (`config_kwargs` + warunkowy `scene_threshold`); usunięte wszystkie 4 antidotum `… if … in locals()` — `qa_verdict_map`/`skipped_qa_steps`/`nano_stats`/`annotations_map` zawsze jawnie zainicjalizowane.
7. **VTD-028 (SSRF w image_enhancer):** `_validate_fetch_url()` — tylko http/https, walidacja hostname przez `getaddrinfo`, odrzucanie loopback/private/link-local/multicast/reserved (127/8, 10/8, 172.16/12, 192.168/16, ::1, fe80::/10, fc00::/7, 169.254/16 w tym 169.254.169.254) przed `urlopen` w `_omniroute_image_call`; `_gemini_text_call`: klucz `?key=` z URL → header `x-goog-api-key`.

## Nowe testy — `tests/test_regression_vtd_runda3.py` (14 testów)

`test_numpy_in_project_dependencies`, `test_cli_has_no_locals_calls`, `test_run_pipeline_signature_25_params`, `test_retry_skips_validation_error_no_choices`, `test_retry_503_retries_with_exponential_sleep`, `test_parse_progress_marker_variants`, `test_execute_task_marker_and_fallback_updates_stage`, `test_stage_notes_mapping_intact`, `test_db_connection_wal_busy_timeout`, `test_db_init_paths_guard_and_wal_table`, `test_validate_fetch_url_blocks_private_and_metadata` (11 złych URL), `test_validate_fetch_url_allows_public_ip_literal`, `test_omniroute_image_call_rejects_ssrf_without_fetch`, `test_gemini_text_call_key_in_header_not_url`.

## Faza 2 — dokumentacja

- `README.md`: badge Docker (python:3.11-slim obok Python 3.10+ z `pyproject`), precyzyjny claim LLM (bez „Vision” — review faktycznie OCR+tekst przez chat completion), fusion opisany jako 3 sygnały (OCR wyłącznie do lokalizacji UI), meeting `DO POTWIERDZENIA` zamiast wymyslania faktów, Results Viewer tylko MD (HTML/DOCX z katalogu), „Local First” z jawnym zakresem wysyłania do providera LLM, autoryzacja Studio (`X-VTD-Token`/cookie, brak `?token=`), nowa sekcja „Additional CLI Capabilities” (frame-qa, annotate, nano-banana, output-mode internal|client-ready|both, rerender, watchdog, qa_verifier, settings).
- `vtd/studio/templates/base.html`: usunięty fałszywy claim „brak sieci poza localhost” (opcjonalne etapy LLM wysyłają dane do skonfigurowanego providera).
- `config.example.yaml`: `scene_threshold: 0.35` (sync z domyślną wartością w `vtd/config.py:96`) + `min_step_seconds: 6.0`.
- `SKILL.md` poza repo (`~/.config/opencode/skills/video-to-doc/SKILL.md`): testy 135, lista naprawionych/otwartych VTD.

Nie ruszono: `AUDYT_KODU.md`, `AUDYT_DOKUMENTACJI_VS_KOD_20260927.md` (untracked, historyczny), raportów historycznych, `.env`.

## Weryfikacja

- `pytest tests/ -q`: **135 passed, 1.78 s** (baseline 121 + 14 nowych), zera fail.
- `ast.parse` na zmienionych plikach py.
- `grep "in locals()" vtd/cli.py` → 0 wystąpień.

## Commity

1. `fix(vtd): Runda 3 — VTD-012/014/018/021/023/027/028 + 14 testów regresyjnych (135 passed)`
2. `docs: Runda 3 — sync README/config.example/base.html ze stanem kodu`

Bez push, bez rebase/reset/stash, bez dotykania `.env`.
