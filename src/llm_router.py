"""
LLM Router
-----------
Single place that decides which LLM to use.

Priority:
  1. Gemini Flash  (fast, cheap, great at structured output)
  2. Ollama local  (fallback on rate-limit / quota / no key)

Call  `invoke_with_fallback(messages)`  anywhere in the codebase and
never think about rate limits again.
"""

from __future__ import annotations

import os
import time

from pathlib import Path
from dotenv import load_dotenv
from langchain_core.messages import BaseMessage

load_dotenv(dotenv_path=Path(__file__).parent.parent / ".env")


# ── Individual LLM builders ────────────────────────────────────────────────────

def _gemini(temperature: float = 0.3):
    from langchain_google_genai import ChatGoogleGenerativeAI

    api_key = os.getenv("GOOGLE_API_KEY", "")
    if not api_key:
        raise EnvironmentError("GOOGLE_API_KEY not set")
    return ChatGoogleGenerativeAI(
        model="gemini-1.5-flash",
        google_api_key=api_key,
        temperature=temperature,
    )


def _ollama(temperature: float = 0.3):
    from langchain_community.chat_models import ChatOllama

    base_url = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434")
    model    = os.getenv("OLLAMA_MODEL",    "qwen2.5:14b")
    return ChatOllama(model=model, base_url=base_url, temperature=temperature)


# ── Rate-limit detector ────────────────────────────────────────────────────────

def _is_rate_limit(exc: Exception) -> bool:
    """Return True for Gemini 429 / quota-exceeded errors."""
    msg = str(exc).lower()
    return any(kw in msg for kw in ("429", "quota", "rate limit", "resource_exhausted"))


# ── Public API ─────────────────────────────────────────────────────────────────

def invoke_with_fallback(
    messages: list[BaseMessage],
    temperature: float = 0.3,
    max_retries: int = 2,
) -> str:
    """
    Try Gemini Flash; fall back to Ollama on rate-limit or hard error.
    Returns the response content as a plain string.
    """
    # ── Attempt Gemini ─────────────────────────────────────────────────────
    for attempt in range(1, max_retries + 1):
        try:
            llm      = _gemini(temperature)
            response = llm.invoke(messages)
            return response.content

        except Exception as exc:
            if _is_rate_limit(exc):
                wait = 2 ** attempt          # 2s, 4s …
                print(f"  [ROUTER] Gemini rate-limit hit — waiting {wait}s (attempt {attempt}/{max_retries})")
                time.sleep(wait)
            else:
                print(f"  [ROUTER] Gemini error: {exc}")
                break   # non-transient error — don't retry, go to fallback

    # ── Fallback: Ollama ───────────────────────────────────────────────────
    model = os.getenv("OLLAMA_MODEL", "qwen2.5:14b")
    print(f"  [ROUTER] Falling back to Ollama ({model})…")
    try:
        llm      = _ollama(temperature)
        response = llm.invoke(messages)
        return response.content
    except Exception as exc:
        raise RuntimeError(
            f"Both Gemini and Ollama failed.\n"
            f"Gemini: check GOOGLE_API_KEY\n"
            f"Ollama: make sure `ollama serve` is running and {model} is pulled\n"
            f"Last error: {exc}"
        ) from exc
