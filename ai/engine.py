"""
AI Engine - sends detected questions to the configured AI provider and returns concise answers.
Supports: Anthropic (Claude), Google Gemini, Groq (LLaMA / Mixtral / Gemma).
Maintains a conversation context window for the duration of the call.
"""
import os
from typing import Optional
import config


SYSTEM_PROMPT = """You are an AI copilot assisting someone during a live client/business call.

Your role:
- Listen to questions being asked during the call
- Provide SHORT, PUNCHY answers that can be read at a glance (3-5 sentences max)
- Use bullet points for lists
- Be factual and confident
- If you need to mention numbers/stats, be precise
- Focus on what would be most immediately useful to say OUT LOUD on the call

Format rules:
- Never use markdown headers
- Keep answers under 80 words
- Lead with the most important point
- End with a one-line "TIP:" if there's a relevant follow-up action
"""


# ── Provider implementations ──────────────────────────────────────────────────

class _AnthropicBackend:
    def __init__(self, api_key: str, model: str):
        import anthropic as _anthropic
        self._client = _anthropic.Anthropic(api_key=api_key)
        self._model = model
        self._AuthError = _anthropic.AuthenticationError
        self._RateLimitError = _anthropic.RateLimitError

    def chat(self, history: list, max_tokens: int = 300) -> str:
        response = self._client.messages.create(
            model=self._model,
            max_tokens=max_tokens,
            system=SYSTEM_PROMPT,
            messages=history,
        )
        return response.content[0].text.strip()

    def wrap_error(self, exc: Exception) -> str:
        if isinstance(exc, self._AuthError):
            return "[ERROR] Invalid Anthropic API key. Check ANTHROPIC_API_KEY in your .env."
        if isinstance(exc, self._RateLimitError):
            return "[WARN] Anthropic rate limit hit. Wait a moment and try again."
        return f"⚠️ Anthropic error: {str(exc)[:100]}"


class _GeminiBackend:
    def __init__(self, api_key: str, model: str):
        import google.generativeai as genai
        genai.configure(api_key=api_key)
        # Gemini expects system instruction differently
        self._genai = genai
        self._model_name = model

    def chat(self, history: list, max_tokens: int = 300) -> str:
        model = self._genai.GenerativeModel(
            model_name=self._model_name,
            system_instruction=SYSTEM_PROMPT,
            generation_config={"max_output_tokens": max_tokens},
        )
        # Convert OpenAI-style history to Gemini format
        gemini_history = []
        for msg in history[:-1]:   # all but the last (current) message
            role = "user" if msg["role"] == "user" else "model"
            gemini_history.append({"role": role, "parts": [msg["content"]]})

        chat = model.start_chat(history=gemini_history)
        response = chat.send_message(history[-1]["content"])
        return response.text.strip()

    def wrap_error(self, exc: Exception) -> str:
        msg = str(exc)
        if "API_KEY" in msg or "api key" in msg.lower():
            return "[ERROR] Invalid Gemini API key. Check GEMINI_API_KEY in your .env."
        if "quota" in msg.lower() or "rate" in msg.lower():
            return "[WARN] Gemini quota/rate limit hit. Wait a moment and try again."
        return f"⚠️ Gemini error: {msg[:100]}"


class _GroqBackend:
    def __init__(self, api_key: str, model: str):
        from groq import Groq, AuthenticationError, RateLimitError
        self._client = Groq(api_key=api_key)
        self._model = model
        self._AuthError = AuthenticationError
        self._RateLimitError = RateLimitError

    def chat(self, history: list, max_tokens: int = 300) -> str:
        messages = [{"role": "system", "content": SYSTEM_PROMPT}] + history
        response = self._client.chat.completions.create(
            model=self._model,
            messages=messages,
            max_tokens=max_tokens,
        )
        return response.choices[0].message.content.strip()

    def wrap_error(self, exc: Exception) -> str:
        if isinstance(exc, self._AuthError):
            return "[ERROR] Invalid Groq API key. Check GROQ_API_KEY in your .env."
        if isinstance(exc, self._RateLimitError):
            return "[WARN] Groq rate limit hit. Wait a moment and try again."
        return f"⚠️ Groq error: {str(exc)[:100]}"


# ── Public AIEngine class ─────────────────────────────────────────────────────

class AIEngine:
    """
    Provider-agnostic AI engine.
    Reads AI_PROVIDER and AI_MODEL from config (set via .env).
    """

    def __init__(self, api_key: Optional[str] = None):
        provider = config.AI_PROVIDER
        model    = config.AI_MODEL

        if provider == "anthropic":
            key = api_key or config.ANTHROPIC_API_KEY
            if not key:
                raise ValueError(
                    "ANTHROPIC_API_KEY not set. Add it to your .env file."
                )
            self._backend = _AnthropicBackend(key, model)

        elif provider == "gemini":
            key = api_key or config.GEMINI_API_KEY
            if not key:
                raise ValueError(
                    "GEMINI_API_KEY not set. Add it to your .env file."
                )
            self._backend = _GeminiBackend(key, model)

        elif provider == "groq":
            key = api_key or config.GROQ_API_KEY
            if not key:
                raise ValueError(
                    "GROQ_API_KEY not set. Add it to your .env file."
                )
            self._backend = _GroqBackend(key, model)

        else:
            raise ValueError(
                f"Unknown AI_PROVIDER '{provider}'. "
                "Choose one of: anthropic, gemini, groq"
            )

        self._provider = provider
        self._model    = model
        self.conversation_history = []
        self.max_history_turns = 10  # Keep last 10 Q&A pairs

        print(f"[AI] Provider: {provider}  |  Model: {model}")

    # ── Public API ────────────────────────────────────────────────

    def answer(self, question: str) -> str:
        """Get a concise answer for a question heard on the call."""
        self.conversation_history.append({
            "role": "user",
            "content": question,
        })

        # Trim history to max turns (2 messages per turn)
        if len(self.conversation_history) > self.max_history_turns * 2:
            self.conversation_history = self.conversation_history[-(self.max_history_turns * 2):]

        try:
            answer_text = self._backend.chat(self.conversation_history)
            self.conversation_history.append({
                "role": "assistant",
                "content": answer_text,
            })
            return answer_text
        except Exception as exc:
            return self._backend.wrap_error(exc)

    def clear_context(self):
        """Clear conversation history (e.g. when starting a new call)."""
        self.conversation_history.clear()

    def set_call_context(self, context: str):
        """
        Optionally prime the AI with context about the call.
        E.g. "This is a sales call for our SaaS product priced at $99/mo..."
        """
        self.conversation_history = [
            {
                "role": "user",
                "content": f"Call context (for reference): {context}",
            },
            {
                "role": "assistant",
                "content": "Got it. I'll keep this context in mind when answering questions.",
            },
        ]

    @property
    def provider(self) -> str:
        return self._provider

    @property
    def model(self) -> str:
        return self._model
