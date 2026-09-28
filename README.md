# Video-to-Doc (VTD) 🎬➡️📄

**Video-to-Doc (VTD)** is an autonomous, local-first engine and Web Studio that converts screen recordings, application walkthroughs, and meeting captures into structured, step-by-step technical documentation, operational manuals, and verified meeting minutes.

[![Python](https://img.shields.io/badge/Python-3.10%2B-blue.svg)](https://python.org)
[![Docker](https://img.shields.io/badge/Docker-python%3A3.11--slim-2496ED.svg)](Dockerfile)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.100%2B-009688.svg)](https://fastapi.tiangolo.com)
[![Whisper](https://img.shields.io/badge/ASR-faster--whisper-orange.svg)](https://github.com/SYSTRAN/faster-whisper)
[![License: MIT](https://img.shields.io/badge/License-MIT-green.svg)](LICENSE)

---

## 🌟 Key Features

- **Multi-Signal Fusion**: Synchronizes three signals — speech segments (faster-whisper), detected speech pauses, and visual scene changes (pHash-deduplicated keyframes) — into coherent procedural steps; on-screen OCR text is used afterwards to locate UI elements for annotations.
- **Visual UI Annotations**: Automatically locates key UI elements using OCR TSV coordinates and draws non-destructive visual guides (focus arrows, bounding boxes, numbered sequence badges, background dimming).
- **Frame Quality Assurance**: Automatically evaluates screenshot readability, rankings frames by Laplacian sharpness variance to select the crispest frame for every step.
- **Universal LLM Support**: Fully compatible with any OpenAI-compatible chat API (OpenAI GPT-4o-mini, OpenRouter, local Ollama, LM Studio, Groq, vLLM, or self-hosted gateways). Optional LLM features (step enrichment, frame QA review, AI annotations) send text/frame data to your configured provider.
- **Multi-Format Export**: Generates clean Markdown (`INSTRUKCJA.md`), standalone responsive HTML with embedded CSS (`INSTRUKCJA.html`), and formatted Microsoft Word documents (`INSTRUKCJA.docx`).
- **Two Operation Modes**:
  - `manual`: Step-by-step procedural manual with action steps, tips, and warnings.
  - `meeting`: Structured protocol with decisions, participants, action items, deadlines, and open questions. Fields that cannot be extracted from the recording are marked **`DO POTWIERDZENIA`** (to confirm), never invented.
- **Dual Interface**:
  - **CLI**: Fast, scriptable, batch-friendly headless execution.
  - **Web Studio**: Modern, lightweight FastAPI dashboard with a persistent task queue, live progress polling, and instant document previews.

---

## 🏗️ Architecture Pipeline

```text
[ Screen Recording (.mp4, .mkv, .avi) ]
                  │
        ┌─────────┴─────────┐
        ▼                   ▼
 [ Audio Track ]     [ Video Frames ]
        │                   │
  silencedetect      scene detection
        │                   │
  faster-whisper       pHash dedup
        │                   │
  Speech Segments     Visual Keyframes
        │                   │
        └─────────┬─────────┘
                  ▼
          [ Signal Fusion ]
    (Aligns speech with visual changes)
                  │
        ┌─────────┴─────────┐
        ▼                   ▼
 [ Frame QA & Sharpness ] [ UI Locator ]
(Laplacian rank & review) (Tesseract TSV)
        │                   │
        └─────────┬─────────┘
                  ▼
         [ Visual Annotator ]
  (Renders arrows, badges, highlights)
                  │
                  ▼
       [ LLM Step Enrichment ]
   (OpenAI / OpenRouter / Ollama)
                  │
                  ▼
 [ Multi-Format Builders: MD, HTML, DOCX ]
```

---

## 🐳 Quick Start with Docker (Recommended)

The easiest way to run Video-to-Doc without installing system packages (`ffmpeg`, `tesseract`, Python venvs) manually is via Docker:

### 1. Using Docker Compose (One-Click)

```bash
# Clone the repository
git clone https://github.com/wujcio1988/video-to-doc.git
cd video-to-doc

# Set your API key in .env or config.yaml
echo "OPENAI_API_KEY=your_key_here" > .env

# Start the Studio in the background
docker compose up -d
```

Open `http://127.0.0.1:9870/` in your browser.

### 2. Using Docker CLI Directly

```bash
docker run -d \
  --name video-to-doc \
  -p 9870:9870 \
  -v $(pwd)/recordings:/app/recordings \
  -v $(pwd)/output:/app/output \
  -v $(pwd)/data:/app/data \
  -e OPENAI_API_KEY=your_key_here \
  ghcr.io/wujcio1988/video-to-doc:latest
```

---

## 📋 System Prerequisites (Manual / Non-Docker)

1. **FFmpeg** (with `ffprobe`) — for audio extraction and scene detection.
2. **Tesseract OCR** — for text detection and coordinate mapping.

### Linux (Ubuntu / Debian / WSL2):
```bash
sudo apt update
sudo apt install -y ffmpeg tesseract-ocr tesseract-ocr-pol tesseract-ocr-eng
```

### macOS:
```bash
brew install ffmpeg tesseract tesseract-lang
```

### Windows:
Install via [winget](https://learn.microsoft.com/en-us/windows/package-manager/winget/) or [Chocolatey](https://chocolatey.org/):
```powershell
winget install Gyan.FFmpeg
winget install UB-Mannheim.TesseractOCR
```

---

## 🚀 Installation

Clone the repository and install dependencies:

```bash
git clone https://github.com/wujcio1988/video-to-doc.git
cd video-to-doc

# Create and activate virtual environment
python3 -m venv .venv
source .venv/bin/activate

# Install package in editable mode
pip install -e ".[dev]"
```

---

## ⚙️ Configuration

VTD is designed to be completely independent and configurable. Copy the example configuration files:

```bash
cp config.example.yaml config.yaml
cp .env.example .env
```

### Connecting Your Preferred LLM Provider

Edit `config.yaml` or set environment variables:

#### Option 1: OpenAI
```yaml
llm:
  provider: "openai"
  base_url: "https://api.openai.com/v1"
  api_key_env: "OPENAI_API_KEY"
  enricher_model: "opencode-go/minimax-m3"
  frame_qa_model: "gpt-4o-mini"
```

#### Option 2: OpenRouter
```yaml
llm:
  provider: "openrouter"
  base_url: "https://openrouter.ai/api/v1"
  api_key_env: "OPENROUTER_API_KEY"
  enricher_model: "deepseek/deepseek-chat"
```

#### Option 3: Local Ollama (100% Offline & Free)
```yaml
llm:
  provider: "ollama"
  base_url: "http://localhost:11434/v1"
  api_key: "ollama"
  enricher_model: "qwen2.5-coder:32b"
```

---

## 💻 Usage

### 1. Command Line Interface (CLI)

Process a recording directly:

```bash
# Basic generation
vtd run recording.mp4 -o ./output --title "New User Onboarding"

# Advanced with LLM enrichment and visual annotations
vtd run recording.mp4 \
  --output ./output \
  --title "ERP Order Entry Procedure" \
  --mode manual \
  --track auto \
  --enrich \
  --frame-qa \
  --annotate

# Meeting mode
vtd run team_sync.mkv \
  --output ./output_meeting \
  --title "Sprint Planning 42" \
  --mode meeting
```

### 2. Web Studio UI

Start the local web dashboard:

```bash
vtd studio --port 9870
```

Open your browser at `http://127.0.0.1:9870/` to access:
- **Dashboard**: Overview of discovered recordings and task queue.
- **New Task**: Interactive form with automatic audio track probe and mode selection.
- **Task Monitor**: Real-time progress polling, stage updates, and live execution logs.
- **Results Viewer**: Instant preview of the generated Markdown report (`INSTRUKCJA.md` / `TRANSKRYPCJA.md`). HTML/DOCX files are written next to it — download them from the output folder.

Studio security: if `VTD_AUTH_TOKEN` is set (required in Docker), all endpoints except `/health` require the `X-VTD-Token` header or the `vtd_token` cookie obtained at `/login` — the `?token=` query parameter is deliberately not accepted to avoid leaking the token into logs. Without a token, Studio binds to `127.0.0.1` only.

### 3. Additional CLI Capabilities

- **Frame QA** (`--frame-qa`): LLM-based review of frame readability with a hard per-run review budget; unclear/wrong frames trigger a single assisted re-crop.
- **AI annotations** (`--annotate`): OCR TSV locating of key UI elements, drawing focus arrows, boxes and numbered badges into a separate non-destructive directory.
- **Image enhancement** (`--nano-banana`, experimental): Gemini/OmniRoute-based frame upscaling of selected frames, with `--nano-auto-accept` to auto-accept.
- **Output mode** (`--output-mode`): `both` (default) copies documents/frames to internal and client-ready locations; `internal` / `client-ready` restrict the manifest accordingly.
- **Rerender** (`vtd rerender`): Regenerate MD/HTML/DOCX documents from an existing output folder without re-running the whole pipeline. It prefers `steps.json` (canonical step + LLM snapshot written by `vtd run`) and falls back to `metadata.json` / `PROMPT_DLA_AGENTA.txt` / `INSTRUKCJA.md` for older outputs.
- **Watchdog**: Optional crash-recovery watchdog for long runs (see `AUDYT_KODU.md` for known limitations).
- **QA verifier**: Manifest-based post-run consistency checks documented in `COMPLETENESS_REPORT.md`.
- **Studio settings page**: Editable pipeline defaults persisted in the local settings store.

---

## 🔒 Security & Privacy

- **Local First (with clear boundaries)**: Video processing, audio extraction, transcription, scene detection and frame extraction run entirely on your local machine. When enabled, optional LLM features (enrichment, frame QA, AI annotations, image enhancement) send extracted text and frames to your configured API provider — nothing is ever sent anywhere else.
- **Manual-First Contract**: All generated documents are stamped as **`DRAFT / FOR VERIFICATION`**. The tool never automatically disseminates or publishes documentation without human sign-off.
- **Non-Destructive**: Original video keyframes are preserved in `frames/` unmodified; visual annotations are saved in separate subdirectories.

---

## 📄 License

This project is licensed under the terms of the [MIT License](LICENSE).
Copyright (c) 2026 Artur Kozłowski.
