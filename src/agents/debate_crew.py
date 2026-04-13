"""
CrewAI Debate Crew  (Bull / Bear / Arbiter)
--------------------------------------------
Three agents sequentially debate a market topic.

LLM priority:
  1. Ollama qwen2.5:14b  (local, private, free)
  2. Gemini Flash        (fallback if Ollama isn't reachable)

The Arbiter gets the Bull + Bear task outputs as context before deciding.
"""

from __future__ import annotations

import os
import sys

_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from dotenv import load_dotenv

load_dotenv()


# ── LLM selector ──────────────────────────────────────────────────────────────

def _pick_llm():
    """
    Return a crewai.LLM pointed at whichever backend is available.
    Tries Ollama first; falls back to Gemini Flash.
    """
    from crewai import LLM

    ollama_url   = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434")
    ollama_model = os.getenv("OLLAMA_MODEL", "qwen2.5:14b")

    # Quick reachability check — non-blocking 2 s timeout
    try:
        import requests as _req
        r = _req.get(f"{ollama_url}/api/tags", timeout=2)
        if r.status_code == 200:
            available = [m.get("name", "") for m in r.json().get("models", [])]
            base_name = ollama_model.split(":")[0]
            if any(base_name in m for m in available):
                print(f"  [DEBATE] Using Ollama ({ollama_model})")
                return LLM(model=f"ollama/{ollama_model}", base_url=ollama_url)
            else:
                print(f"  [DEBATE] Ollama running but {ollama_model} not pulled — falling back")
        else:
            print("  [DEBATE] Ollama not reachable — falling back to Gemini Flash")
    except Exception:
        print("  [DEBATE] Ollama not reachable — falling back to Gemini Flash")

    # Gemini Flash fallback via LiteLLM routing
    api_key = os.getenv("GOOGLE_API_KEY", "")
    if not api_key:
        raise EnvironmentError(
            "Neither Ollama (qwen2.5:14b) nor GOOGLE_API_KEY is available.\n"
            "Set GOOGLE_API_KEY in your .env or start Ollama."
        )
    print("  [DEBATE] Using Gemini Flash (gemini/gemini-1.5-flash)")
    return LLM(model="gemini/gemini-1.5-flash", api_key=api_key)


# ── Agent / Task builders ──────────────────────────────────────────────────────

def _build_crew(topic: str, context_signals: str, llm):
    from crewai import Agent, Crew, Task

    # ── Bull Analyst ──────────────────────────────────────────────────────────
    bull = Agent(
        role="Bull Analyst",
        goal=(
            "Construct the strongest possible argument for why the YES outcome "
            "is likely for the given prediction market question."
        ),
        backstory=(
            "You are an optimistic market analyst who excels at identifying "
            "tail-wind signals, historical precedents favouring the bullish "
            "case, and underpriced probabilities. You speak in crisp, "
            "evidence-backed paragraphs."
        ),
        llm=llm,
        verbose=True,
        max_iter=3,
        allow_delegation=False,
    )

    # ── Bear Analyst ──────────────────────────────────────────────────────────
    bear = Agent(
        role="Bear Analyst",
        goal=(
            "Construct the strongest possible argument for why the NO outcome "
            "is likely for the given prediction market question."
        ),
        backstory=(
            "You are a sceptical risk analyst who excels at spotting "
            "over-hyped narratives, base-rate reality checks, and structural "
            "reasons markets often overestimate dramatic outcomes. "
            "You are direct and data-grounded."
        ),
        llm=llm,
        verbose=True,
        max_iter=3,
        allow_delegation=False,
    )

    # ── Arbiter ───────────────────────────────────────────────────────────────
    arbiter = Agent(
        role="Arbiter",
        goal=(
            "Weigh the bull and bear cases objectively and issue a final "
            "verdict: YES, NO, or ABSTAIN, with a one-paragraph rationale."
        ),
        backstory=(
            "You are an impartial superforecaster trained on Tetlock's Good "
            "Judgment Project principles. You update on evidence, avoid "
            "narrative bias, and always give a concrete position with "
            "explicit uncertainty acknowledgement."
        ),
        llm=llm,
        verbose=True,
        max_iter=3,
        allow_delegation=False,
    )

    # ── Tasks ─────────────────────────────────────────────────────────────────
    signal_block = f"\nRelevant signals:\n{context_signals}\n"

    bull_task = Task(
        description=(
            f"Prediction market question:\n  {topic}\n"
            f"{signal_block}\n"
            "Write 2–3 tight paragraphs making the BULL (YES) case. "
            "Reference at least one specific signal from the list above."
        ),
        expected_output=(
            "2–3 paragraphs of bullish reasoning tied to specific evidence. "
            "No headers, no bullet lists — prose only."
        ),
        agent=bull,
    )

    bear_task = Task(
        description=(
            f"Prediction market question:\n  {topic}\n"
            f"{signal_block}\n"
            "Write 2–3 tight paragraphs making the BEAR (NO) case. "
            "Reference at least one specific signal and explain why it "
            "is being over-interpreted by bulls."
        ),
        expected_output=(
            "2–3 paragraphs of bearish reasoning tied to specific evidence. "
            "No headers, no bullet lists — prose only."
        ),
        agent=bear,
    )

    arbiter_task = Task(
        description=(
            f"Prediction market question:\n  {topic}\n\n"
            "You have just received the Bull and Bear analyses (provided as "
            "context). Weigh both sides and deliver:\n"
            "  POSITION: YES | NO | ABSTAIN\n"
            "  VERDICT: one paragraph explaining your reasoning and any "
            "key uncertainties.\n"
            "Be decisive — ABSTAIN only if evidence is genuinely too thin."
        ),
        expected_output=(
            "POSITION: <YES|NO|ABSTAIN>\n"
            "VERDICT: <one-paragraph rationale>"
        ),
        agent=arbiter,
        context=[bull_task, bear_task],
    )

    crew = Crew(
        agents=[bull, bear, arbiter],
        tasks=[bull_task, bear_task, arbiter_task],
        verbose=True,
    )

    return crew, bull_task, bear_task, arbiter_task


# ── Public API ─────────────────────────────────────────────────────────────────

def run_debate(topic: str, context_signals: str) -> dict:
    """
    Run the 3-agent debate and return a structured result dict:
    {
      "bull":     str,
      "bear":     str,
      "verdict":  str,
      "position": "YES" | "NO" | "ABSTAIN",
    }
    """
    import re

    print(f"\n  [DEBATE] Topic: {topic[:80]}")

    try:
        llm = _pick_llm()
        crew, bull_task, bear_task, arbiter_task = _build_crew(
            topic, context_signals, llm
        )
        result = crew.kickoff()

        # Extract individual task outputs
        bull_out    = str(getattr(bull_task,    "output", "") or "")
        bear_out    = str(getattr(bear_task,    "output", "") or "")
        arbiter_out = str(getattr(arbiter_task, "output", "") or str(result))

        # Parse position from arbiter output
        pos_match = re.search(
            r"POSITION\s*:\s*(YES|NO|ABSTAIN)", arbiter_out, re.IGNORECASE
        )
        position = pos_match.group(1).upper() if pos_match else _infer_position(arbiter_out)

        # Extract verdict text
        verdict_match = re.search(
            r"VERDICT\s*:\s*(.+)", arbiter_out, re.DOTALL | re.IGNORECASE
        )
        verdict = verdict_match.group(1).strip() if verdict_match else arbiter_out[:400]

        return {
            "bull":     bull_out[:600],
            "bear":     bear_out[:600],
            "verdict":  verdict[:500],
            "position": position,
        }

    except Exception as exc:
        print(f"  [DEBATE] CrewAI error: {exc}")
        return _fallback_debate(topic, context_signals)


def _infer_position(text: str) -> str:
    text_lower = text.lower()
    yes_score = text_lower.count("yes") + text_lower.count("likely") + text_lower.count("bullish")
    no_score  = text_lower.count("no")  + text_lower.count("unlikely") + text_lower.count("bearish")
    if yes_score > no_score:
        return "YES"
    if no_score > yes_score:
        return "NO"
    return "ABSTAIN"


def _fallback_debate(topic: str, context_signals: str) -> dict:
    """Returns a minimal result if CrewAI completely fails."""
    return {
        "bull":     "Bullish signals present in recent news flow.",
        "bear":     "Insufficient certainty to commit to the YES outcome.",
        "verdict":  "CrewAI debate unavailable — manual review required.",
        "position": "ABSTAIN",
    }
