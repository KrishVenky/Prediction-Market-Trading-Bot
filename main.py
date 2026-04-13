"""
PolySignal — Demo Entry Point
==============================
Runs the full pipeline:
  RSS scrape  →  Gemini Flash parse  →  CrewAI debate  →  Confidence score

Usage:
  python main.py                         # interactive topic picker
  python main.py "Will BTC hit 100k?"    # pass topic directly
"""

from __future__ import annotations

import sys
import os

# Ensure project root is importable from any working directory
_ROOT = os.path.dirname(os.path.abspath(__file__))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from dotenv import load_dotenv

load_dotenv()

# ── Demo topics (for interactive picker) ──────────────────────────────────────

DEMO_TOPICS = [
    "Will the US Federal Reserve cut interest rates before July 2025?",
    "Will Trump's tariffs cause US GDP growth to go negative in 2025?",
    "Will Bitcoin exceed $100,000 before the end of 2025?",
    "Will there be a US government shutdown before October 2025?",
    "Will the S&P 500 hit a new all-time high before June 2025?",
]

_BANNER = "═" * 62


def _check_env():
    """Warn about missing keys before starting the pipeline."""
    missing = []
    if not os.getenv("GOOGLE_API_KEY"):
        missing.append("GOOGLE_API_KEY  (Gemini Flash — required)")
    if missing:
        print("\n  [WARNING] Missing environment variables:")
        for m in missing:
            print(f"    • {m}")
        print("  Copy .env.example → .env and fill in your keys.\n")
        confirm = input("  Continue anyway? [y/N] ").strip().lower()
        if confirm != "y":
            sys.exit(0)


def _pick_topic() -> str:
    """Interactive topic picker shown when no CLI arg is given."""
    print(f"\n  Select a demo topic or enter your own:\n")
    for i, t in enumerate(DEMO_TOPICS, 1):
        print(f"    {i}. {t}")
    print(f"\n    0. Enter a custom topic")
    print()

    while True:
        raw = input("  Choice [1]: ").strip()
        if not raw:
            return DEMO_TOPICS[0]
        if raw.isdigit():
            n = int(raw)
            if 1 <= n <= len(DEMO_TOPICS):
                return DEMO_TOPICS[n - 1]
            if n == 0:
                custom = input("  Your topic: ").strip()
                if custom:
                    return custom
        else:
            # Treat any non-digit input as a custom topic
            return raw


# ── Main ───────────────────────────────────────────────────────────────────────

def main():
    print(f"\n{_BANNER}")
    print("  POLYSIGNAL  ·  Prediction Market Intelligence")
    print("  Stack  :  RSS → LangGraph → Gemini Flash → CrewAI → Score")
    print("  LLMs   :  Gemini 1.5 Flash (parse + score)  |  qwen2.5:14b (debate)")
    print(f"{_BANNER}")

    _check_env()

    # ── Pick topic ────────────────────────────────────────────────────────────
    if len(sys.argv) > 1:
        topic = " ".join(sys.argv[1:])
        print(f"\n  Topic (from CLI): {topic}")
    else:
        topic = _pick_topic()

    print(f"\n  Running pipeline for:\n  \"{topic}\"\n")

    # ── Run pipeline ──────────────────────────────────────────────────────────
    from src.pipeline.langgraph_pipeline import run_pipeline

    try:
        final_state = run_pipeline(topic)

        # Summary already printed by node_score — just confirm completion
        print(f"\n  [PIPELINE COMPLETE]")
        conf = final_state.get("confidence_score", 0)
        edge = final_state.get("edge", 0)
        pos  = final_state.get("debate_result", {}).get("position", "?")
        print(f"  Final: {pos}  |  Confidence {conf:.1%}  |  Edge {edge:+.1%}")
        print()

    except KeyboardInterrupt:
        print("\n\n  Interrupted by user.\n")
        sys.exit(0)
    except Exception as exc:
        print(f"\n  [PIPELINE ERROR] {exc}")
        import traceback
        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    main()
