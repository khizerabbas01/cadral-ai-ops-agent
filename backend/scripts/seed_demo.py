"""Loads replayed sample traffic so the dashboard has a history to show. For demo environments only.

The messages go through the normal pipeline (router -> agent -> tools -> database). By default the agents run in
offline mode for this (deterministic fallbacks, no LLM calls), so it takes seconds and uses none of a free-tier
quota; add --live to use the configured LLM. Only the timestamps are spread over the past days. No human reviews
are invented and nothing is sent anywhere. The dashboard shows a "sample traffic" note while this data is present.

    cd backend
    python -m scripts.seed_demo                      # ~150 conversations over 14 days, offline mode
    python -m scripts.seed_demo --days 30 --conversations 300
    python -m scripts.seed_demo --live --conversations 20 --delay 10   # real LLM, paced for a free tier

Hosted demos load the same data automatically on first boot (DEMO_SEED_ON_BOOT, see app/config.py).
"""
from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.demo import load_sample_traffic  # noqa: E402
from app.rag import kb  # noqa: E402
from app.seed import seed  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--days", type=int, default=14)
    ap.add_argument("--conversations", type=int, default=150)
    ap.add_argument("--delay", type=float, default=0.0, help="seconds between messages (for --live on a free tier)")
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--live", action="store_true", help="use the configured LLM instead of offline mode")
    ap.add_argument("--force", action="store_true", help="add more even if the database already has conversations")
    args = ap.parse_args()
    logging.basicConfig(level=logging.ERROR)

    seed()
    kb.build()
    result = load_sample_traffic(days=args.days, conversations=args.conversations, seed=args.seed, delay=args.delay,
                                 live=args.live, force=args.force)
    if not result["loaded"]:
        print(f"Nothing loaded: {result.get('reason')} (use --force to add more).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
