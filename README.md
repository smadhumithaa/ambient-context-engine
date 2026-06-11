# ambient-context-engine

A modular Python pipeline that captures ambient audio in real-time, transcribes speech using local or cloud-based STT models, and routes detected utterances through a configurable LLM backend to generate contextual responses — displayed in a lightweight always-on-top desktop overlay.

## Architecture

```
Microphone → Audio Buffer → VAD → STT (Whisper) → NLP Filter → LLM → Overlay UI
```

## Components

| Module | Description |
|--------|-------------|
| `audio/listener.py` | Real-time audio capture, VAD, buffering |
| `ai/engine.py` | Multi-provider LLM client (pluggable backend) |
| `overlay/window.py` | Borderless desktop overlay (WDA_EXCLUDEFROMCAPTURE) |
| `config.py` | Environment-based configuration loader |

## Supported LLM Backends

- **Groq** — LLaMA 3.3, Mixtral, Gemma
- **Anthropic** — Claude Sonnet, Haiku, Opus
- **Google Gemini** — 2.0 Flash, 1.5 Pro

## Supported STT Backends

- **faster-whisper** — local inference, free, offline-capable
- **OpenAI Whisper API** — cloud-based, `whisper-1`

## Setup

```bash
# 1. Clone and create venv
python -m venv venv
.\venv\Scripts\Activate.ps1   # Windows

# 2. Install dependencies
pip install -r requirements.txt

# 3. Configure
cp .env.example .env
# Edit .env with your preferred provider + API key

# 4. Run
python main.py
```

## Configuration

Copy `.env.example` to `.env` and set:

```ini
AI_PROVIDER=groq              # groq | anthropic | gemini
AI_MODEL=llama-3.3-70b-versatile
GROQ_API_KEY=your_key_here

STT_PROVIDER=faster-whisper   # faster-whisper | whisper
STT_MODEL=base                # tiny | base | small | medium | large-v2
```

## Requirements

- Python 3.10+
- Windows 10 (build 19041+) for overlay capture exclusion
- Microphone access
