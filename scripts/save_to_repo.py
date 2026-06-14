#!/usr/bin/env python3
"""
Fetches tweets via twikit and saves them as JSONL files inside the repo:

    data/tweets/<source>/YYYY-MM-DD.jsonl

Each line is one tweet serialised as JSON. Runs are idempotent: tweet IDs
already present in today's file are skipped so no duplicates accumulate.

Run with:
    python -m scripts.save_to_repo [--source all_feeds] [--max 100]
"""

import argparse
import asyncio
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

# Make sure the repo root is on sys.path so `from scripts.fetch_tweets`
# works whether invoked as `python -m scripts.save_to_repo` or directly.
_REPO_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(_REPO_ROOT))

from dotenv import load_dotenv

load_dotenv()

DATA_DIR = _REPO_ROOT / "data" / "tweets"


# ---------------------------------------------------------------------------
# File helpers
# ---------------------------------------------------------------------------

def _existing_ids(path: Path) -> set[str]:
    if not path.exists():
        return set()
    ids: set[str] = set()
    with path.open(encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                try:
                    ids.add(json.loads(line)["id"])
                except Exception:
                    pass
    return ids


def _append_tweets(tweets: list[dict], source: str, date_str: str) -> int:
    out_dir = DATA_DIR / source
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"{date_str}.jsonl"

    existing = _existing_ids(out_path)
    new = [t for t in tweets if t.get("id") and t["id"] not in existing]

    if not new:
        print(f"  [{source}] no new tweets for {date_str}")
        return 0

    with out_path.open("a", encoding="utf-8") as f:
        for t in new:
            f.write(json.dumps(t, ensure_ascii=False) + "\n")

    print(f"  [{source}] +{len(new)} tweets → {out_path.relative_to(_REPO_ROOT)}")
    return len(new)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

async def run(source: str, max_tweets: int) -> int:
    from scripts.fetch_tweets import (
        get_client,
        fetch_for_you,
        fetch_following,
        fetch_own_tweets,
    )

    username = os.getenv("TWITTER_USERNAME", "")
    date_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    plans: dict[str, list[str]] = {
        "for_you":   ["for_you"],
        "following": ["following"],
        "mine":      ["mine"],
        "all_feeds": ["for_you", "following"],
        "all":       ["for_you", "following", "mine"],
    }
    kinds = plans.get(source)
    if kinds is None:
        print(f"ERROR: unknown source '{source}'")
        sys.exit(1)

    client = await get_client()

    total = 0
    for kind in kinds:
        if kind == "for_you":
            tweets = await fetch_for_you(client, max_tweets)
        elif kind == "following":
            tweets = await fetch_following(client, max_tweets)
        elif kind == "mine":
            tweets = await fetch_own_tweets(client, username, max_tweets)
        else:
            continue
        total += _append_tweets(tweets, kind, date_str)

    print(f"\nDone — {total} new tweets written to {DATA_DIR.relative_to(_REPO_ROOT)}/")
    return total


def main():
    parser = argparse.ArgumentParser(
        description="Fetch tweets and save them as JSONL files in the repo."
    )
    parser.add_argument(
        "--source",
        choices=["for_you", "following", "mine", "all_feeds", "all"],
        default="all_feeds",
        help="Which feed(s) to fetch (default: all_feeds = for_you + following)",
    )
    parser.add_argument(
        "--max",
        type=int,
        default=100,
        help="Max tweets per source (default: 100)",
    )
    args = parser.parse_args()
    asyncio.run(run(args.source, args.max))


if __name__ == "__main__":
    main()
