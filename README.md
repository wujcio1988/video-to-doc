# Video-to-Doc (VTD) 🎬➡️📄

**Video-to-Doc (VTD)** is an autonomous, local-first engine and Web Studio that converts screen recordings, application walkthroughs, and meeting captures into structured, step-by-step technical documentation, operational manuals, and verified meeting minutes.

[![Python](https://img.shields.io/badge/Python-3.10%2B-blue.svg)](https://python.org)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.100%2B-009688.svg)](https://fastapi.tiangolo.com)
[![Whisper](https://img.shields.io/badge/ASR-faster--whisper-orange.svg)](https://github.com/SYSTRAN/faster-whisper)
[![License: MIT](https://img.shields.io/badge/License-MIT-green.svg)](LICENSE)

---

## 🌟 Key Features

- **Multi-Signal Fusion**: Synchronizes audio narration, speech pauses, screen scene changes, and on-screen text (OCR) into coherent procedural steps.
- **Visual UI Annotations**: Automatically locates key UI elements using OCR TSV coordinates and draws non-destructive visual guides (focus arrows, bounding boxes, numbered sequence badges, background dimming).
- **Frame Quality Assurance**: Automatically evaluates screenshot readability, rankings frames by Laplacian sharpness variance to select the crispest frame for every step.
- **Universal LLM & Vision Support**: Fully compatible with any OpenAI-compatible API (OpenAI GPT-4o/4o-mini, OpenRouter, local Ollama, LM Studio, Groq, vLLM, or self-hosted gateways).
- **Multi-Format Export**: Generates clean Markdown (`INSTRUKCJA.md`), standalone responsive HTML with embedded CSS (`INSTRUKCJA.html`), and formatted Microsoft Word documents (`INSTRUKCJA.docx`).
- **Two Operation Modes**:
  - `manual`: Step-by-step procedural manual with action steps, tips, and warnings.
  - `meeting`: Structured protocol with decisions, participants, action items, deadlines, and open questions.
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

## 📋 System Prerequisites

VTD relies on system-level multimedia tools:

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
  enricher_model: "gpt-4o-mini"
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
- **Results Viewer**: Instant side-by-side preview of generated Markdown, HTML, and Word outputs.

---

## 🔒 Security & Privacy

- **Local First**: All video processing, audio extraction, transcription, and frame extraction occur entirely on your local machine.
- **Manual-First Contract**: All generated documents are stamped as **`DRAFT / FOR VERIFICATION`**. The tool never automatically disseminates or publishes documentation without human sign-off.
- **Non-Destructive**: Original video keyframes are preserved in `frames/` unmodified; visual annotations are saved in separate subdirectories.

---

## 📄 License

This project is licensed under the terms of the [MIT License](LICENSE).
Copyright (c) 2026 Artur Kozłowski.
