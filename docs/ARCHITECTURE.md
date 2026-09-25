# Architecture

VTD is a standalone, local-first pipeline. `vtd run` extracts the selected audio track, transcribes it with local `faster-whisper`, extracts/deduplicates video frames, fuses speech and visual signals, and renders Markdown/HTML/DOCX drafts.

```text
video -> ffmpeg audio -> local Whisper -> transcription_segments.json
      -> scene frames -> dedup -> provenance-aware fusion
      -> optional Gemini semantic candidates (env key only; never evidence)
      -> manual/meeting renderer -> local DRAFT artifacts
```

The pipeline is fail-closed: unavailable optional services produce an explicit status and do not silently replace local transcription or invent frame provenance. Track 3 is labelled system audio and is not treated as a client speaker. Output is a draft for manual verification; no delivery or client upload is implemented.

The package preserves the `vtd` API and CLI namespace. The source implementation was migrated from the internal pipeline with imports rewritten to `vtd`; internal-only integrations not included in this repository remain out of scope.
