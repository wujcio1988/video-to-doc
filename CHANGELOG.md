# Changelog

## Unreleased

- Migrated the verified local Whisper transcription path into the standalone `vtd` package.
- Added model/device/track controls and `transcription_segments.json` provenance artifacts.
- Added optional fail-closed Gemini semantic video map using environment-only credentials.
- Preserved manual and meeting modes and draft-only/manual-verification status.
- Added architecture and operations documentation, secret-free environment example, and CI workflow.
- Standalone test suite currently covers the migrated compatibility surface; internal-only modules remain out of scope.
