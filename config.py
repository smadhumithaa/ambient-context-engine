"""
Config loader - reads AI provider, model, and STT settings from .env file.
"""
import sys

# Force UTF-8 output on Windows so emoji in print() don't crash
if sys.stdout and hasattr(sys.stdout, 'reconfigure'):
    try:
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    except Exception:
        pass
import os
from pathlib import Path


def _load_env_file():
    """Load .env file from project root into os.environ (simple parser, no deps)."""
    env_path = Path(__file__).parent / ".env"
    if not env_path.exists():
        return
    with open(env_path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if "=" not in line:
                continue
            key, _, value = line.partition("=")
            key = key.strip()
            value = value.strip()
            # Only set if not already in environment (env vars win over .env)
            if key and key not in os.environ:
                os.environ[key] = value


# Load .env on import
_load_env_file()

# ── AI provider & model ───────────────────────────────────────────
AI_PROVIDER: str = os.environ.get("AI_PROVIDER", "anthropic").lower().strip()

_DEFAULT_MODELS = {
    "anthropic": "claude-sonnet-4-20250514",
    "gemini":    "gemini-2.0-flash",
    "groq":      "llama-3.3-70b-versatile",
}
AI_MODEL: str = os.environ.get("AI_MODEL", _DEFAULT_MODELS.get(AI_PROVIDER, "")).strip()

# API keys
ANTHROPIC_API_KEY: str = os.environ.get("ANTHROPIC_API_KEY", "")
GEMINI_API_KEY:    str = os.environ.get("GEMINI_API_KEY", "")
GROQ_API_KEY:      str = os.environ.get("GROQ_API_KEY", "")
OPENAI_API_KEY:    str = os.environ.get("OPENAI_API_KEY", "")

# ── STT provider & model ──────────────────────────────────────────
STT_PROVIDER: str = os.environ.get("STT_PROVIDER", "faster-whisper").lower().strip()
STT_MODEL:    str = os.environ.get("STT_MODEL", "base").strip()

VALID_AI_PROVIDERS  = {"anthropic", "gemini", "groq"}
VALID_STT_PROVIDERS = {"faster-whisper", "whisper"}


def validate():
    """Validate config and print a summary. Raises ValueError on fatal issues."""
    errors = []

    if AI_PROVIDER not in VALID_AI_PROVIDERS:
        errors.append(
            f"AI_PROVIDER='{AI_PROVIDER}' is invalid. "
            f"Choose one of: {', '.join(sorted(VALID_AI_PROVIDERS))}"
        )
    else:
        key_map = {
            "anthropic": ("ANTHROPIC_API_KEY", ANTHROPIC_API_KEY),
            "gemini":    ("GEMINI_API_KEY",    GEMINI_API_KEY),
            "groq":      ("GROQ_API_KEY",      GROQ_API_KEY),
        }
        key_name, key_val = key_map[AI_PROVIDER]
        if not key_val:
            errors.append(
                f"{key_name} is not set. "
                f"Add it to your .env file (AI_PROVIDER={AI_PROVIDER})."
            )

    if STT_PROVIDER not in VALID_STT_PROVIDERS:
        errors.append(
            f"STT_PROVIDER='{STT_PROVIDER}' is invalid. "
            f"Choose one of: {', '.join(sorted(VALID_STT_PROVIDERS))}"
        )
    elif STT_PROVIDER == "whisper" and not OPENAI_API_KEY:
        errors.append(
            "OPENAI_API_KEY is not set. "
            "It's required when STT_PROVIDER=whisper."
        )

    if errors:
        raise ValueError("\n".join(f"  [ERROR] {e}" for e in errors))

    # Success summary
    print(f"[Config] OK  AI provider  : {AI_PROVIDER}  (model: {AI_MODEL})")
    print(f"[Config] OK  STT provider : {STT_PROVIDER}  (model: {STT_MODEL})")
