"""
LLM Router  —  PolySignal
--------------------------
Unified fallback chain so the pipeline never dies on a quota error.

Priority:
  1. Gemini  (gemini-3-flash-preview → gemini-2.0-flash → gemini-2.0-flash-lite)
  2. Groq    (llama-3.3-70b  — massive free-tier RPM, near-instant)
  3. Ollama  (local qwen2.5:14b — zero cost, needs `ollama serve`)

Usage:
    from src.llm_router import invoke_with_fallback
    text = invoke_with_fallback([HumanMessage(content="...")])
"""

from __future__ import annotations

import os
import time
from pathlib import Path

from dotenv import load_dotenv
from langchain_core.messages import BaseMessage

load_dotenv(dotenv_path=Path(__file__).parent.parent / ".env")

# ── Model preference list (tried left-to-right) ───────────────────────────────
_GEMINI_MODELS = [
    "gemini-2.0-flash",
    "gemini-2.0-flash-lite",
    "gemini-2.5-flash",
]


# ── LLM builders ──────────────────────────────────────────────────────────────

def _gemini(model: str, temperature: float = 0.3):
    from langchain_google_genai import ChatGoogleGenerativeAI
    api_key = os.getenv("GOOGLE_API_KEY", "")
    if not api_key:
        raise EnvironmentError("GOOGLE_API_KEY not set")
    return ChatGoogleGenerativeAI(model=model, google_api_key=api_key, temperature=temperature)


def _groq(temperature: float = 0.3):
    from langchain_groq import ChatGroq
    api_key = os.getenv("GROQ_API_KEY", "")
    if not api_key or "your_groq" in api_key:
        raise EnvironmentError("GROQ_API_KEY not set")
    model = os.getenv("GROQ_MODEL", "llama-3.3-70b-versatile")
    return ChatGroq(model=model, groq_api_key=api_key, temperature=temperature)


def _ollama(temperature: float = 0.3):
    from langchain_community.chat_models import ChatOllama
    base_url = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434")
    model    = os.getenv("OLLAMA_MODEL",    "qwen2.5:14b")
    return ChatOllama(model=model, base_url=base_url, temperature=temperature)


# ── Error classifiers ──────────────────────────────────────────────────────────

def _is_rate_limit(exc: Exception) -> bool:
    msg = str(exc).lower()
    return any(k in msg for k in ("429", "quota", "rate_limit", "rate limit",
                                   "resource_exhausted", "too many requests",
                                   "tokens per minute"))

def _is_not_found(exc: Exception) -> bool:
    msg = str(exc).lower()
    return any(k in msg for k in ("404", "not_found", "not found",
                                   "model not found", "does not exist"))


# ── Public API ─────────────────────────────────────────────────────────────────

def invoke_with_fallback(
    messages: list[BaseMessage],
    temperature: float = 0.3,
    max_retries: int = 2,
) -> str:
    """
    Try Gemini → Groq → Ollama, with smart per-model retry logic.
    Returns the response content as a plain string.
    """
    last_exc: Exception | None = None

    # ── 1. Gemini ──────────────────────────────────────────────────────────────
    for model in _GEMINI_MODELS:
        for attempt in range(1, max_retries + 1):
            try:
                resp = _gemini(model, temperature).invoke(messages)
                active_model = model
                _log_success(model, attempt)
                return resp.content
            except Exception as exc:
                last_exc = exc
                if _is_not_found(exc):
                    print(f"  [ROUTER] Gemini/{model} not available — skipping")
                    break
                elif _is_rate_limit(exc):
                    wait = 4 ** attempt    # 4s, 16s
                    print(f"  [ROUTER] Gemini/{model} rate-limited — waiting {wait}s ({attempt}/{max_retries})")
                    time.sleep(wait)
                    if attempt == max_retries:
                        print(f"  [ROUTER] Gemini/{model} quota exhausted — next model")
                else:
                    print(f"  [ROUTER] Gemini/{model}: {str(exc)[:80]} — skipping")
                    break

    # ── 2. Groq ────────────────────────────────────────────────────────────────
    groq_model = os.getenv("GROQ_MODEL", "llama-3.3-70b-versatile")
    print(f"  [ROUTER] Trying Groq ({groq_model})…")
    try:
        resp = _groq(temperature).invoke(messages)
        print(f"  [ROUTER] Groq OK")
        return resp.content
    except EnvironmentError:
        print("  [ROUTER] GROQ_API_KEY not set — skipping")
    except Exception as exc:
        last_exc = exc
        if _is_rate_limit(exc):
            print(f"  [ROUTER] Groq rate-limited: {str(exc)[:80]}")
        else:
            print(f"  [ROUTER] Groq error: {str(exc)[:80]}")

    # ── 3. Ollama ──────────────────────────────────────────────────────────────
    ollama_model = os.getenv("OLLAMA_MODEL", "qwen2.5:14b")
    print(f"  [ROUTER] Trying Ollama ({ollama_model})…")
    try:
        resp = _ollama(temperature).invoke(messages)
        print(f"  [ROUTER] Ollama OK")
        return resp.content
    except Exception as exc:
        raise RuntimeError(
            f"All LLMs failed.\n"
            f"  Gemini : quota exhausted — check GOOGLE_API_KEY\n"
            f"  Groq   : add GROQ_API_KEY to .env (free at console.groq.com)\n"
            f"  Ollama : run `ollama serve && ollama pull {ollama_model}`\n"
            f"  Last error: {last_exc}"
        ) from exc


def _log_success(model: str, attempt: int):
    if attempt > 1 or model != _GEMINI_MODELS[0]:
        print(f"  [ROUTER] OK — {model} (attempt {attempt})")
