# Operations

## Local run

Install FFmpeg (and Tesseract if OCR annotations are enabled), create a virtual environment, then install `pip install -e '.[dev]'`. Use `vtd run recording.mkv --output ./output --model small --device cpu --track auto --mode manual` for a smoke-sized run. CUDA uses `--device cuda` and a compatible faster-whisper installation.

Always provide explicit metadata (`--client`, `--process`, `--module`, `--environment`, `--author`) when producing a deliverable. Use `--mode meeting` for minutes and `--mode manual` for procedures. Never use a recording filename as client truth.

## Artifacts and review

Review `metadata.json`, `transcription_segments.json`, `manifest.json`, `COMPLETENESS_REPORT.md`, and the rendered documents. Confirm timestamps and frame provenance locally. Treat all output as `DRAFT` / `DO WERYFIKACJI`; VTD does not assert ERP correctness or send files.

## Gemini

Semantic mapping is optional. Set `GEMINI_API_KEY` (or `GOOGLE_API_KEY`) only in the process environment and pass `--semantic-video-map`. Keys are never written to artifacts or logs. If Gemini is unavailable, continue with local outputs and inspect the explicit unavailable status.

## Safety and cleanup

Do not place recordings, customer directories, caches, generated output, `.env`, or credentials in Git. Keep generated files under ignored directories. Do not enable unattended upload/watchdog behavior. For a release check, run `python3 -m py_compile $(find vtd -name '*.py')` and `pytest -q`.

## Limitations

Whisper quality depends on audio track and model; diarization is optional and may be unavailable. Semantic candidates require human verification. The standalone repository intentionally excludes internal Hermes/ERP integrations and client data.
