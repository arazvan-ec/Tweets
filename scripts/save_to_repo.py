#!/usr/bin/env python3
"""
Fetches tweets via twikit and saves them as JSON files inside the repo.

Output layout:
  data/tweets/YYYY-MM-DD/for_you_HHMMSS.json   — full snapshot of each run
  data/tweets/YYYY-MM-DD/following_HHMMSS.json
  data/tweets/YYYY-MM-DD/mine_HHMMSS.json
  data/tweets/all.jsonl                          — master file, one tweet per line,
                                                   deduplicated by tweet id

Usage:
  python scripts/save_to_repo.py                   # for_you + following
  python scripts/save_to_repo.py --source for_you
  python scripts/save_to_repo.py --source all      # for_you + following + mine
  python scripts/save_to_repo.py --max 200
"""

import argparse
import asyncio
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

REPO_ROOT = Path(__file__).resolve().parent.parent
TWEETS_DIR = REPO_ROOT / "data" / "tweets"
ALL_JSONL = TWEETS_DIR / "all.jsonl"


# ---------------------------------------------------------------------------
# Persistence helpers
# ---------------------------------------------------------------------------

def _load_seen_ids() -> set[str]:
    """Returns tweet IDs already present in all.jsonl."""
    seen = set()
    if ALL_JSONL.exists():
        with ALL_JSONL.open("r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    try:
                        seen.add(json.loads(line)["id"])
                    except Exception:
                        pass
    return seen


def _append_new_tweets(tweets: list[dict], seen_ids: set[str]) -> int:
    """Appends tweets not yet in all.jsonl. Returns count of new tweets added."""
    new_tweets = [t for t in tweets if t.get("id") and t["id"] not in seen_ids]
    if not new_tweets:
        return 0
    ALL_JSONL.parent.mkdir(parents=True, exist_ok=True)
    with ALL_JSONL.open("a", encoding="utf-8") as f:
        for t in new_tweets:
            f.write(json.dumps(t, ensure_ascii=False, default=str) + "\n")
    for t in new_tweets:
        seen_ids.add(t["id"])
    return len(new_tweets)


def _save_snapshot(tweets: list[dict], source: str, timestamp: datetime) -> Path:
    """Saves a full snapshot JSON file for this run."""
    date_dir = TWEETS_DIR / timestamp.strftime("%Y-%m-%d")
    date_dir.mkdir(parents=True, exist_ok=True)
    filename = f"{source}_{timestamp.strftime('%H%M%S')}.json"
    path = date_dir / filename
    with path.open("w", encoding="utf-8") as f:
        json.dump(tweets, f, ensure_ascii=False, indent=2, default=str)
    return path


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

async def run(source: str, max_tweets: int):
    # Import here so patches in fetch_tweets.py apply first
    from scripts.fetch_tweets import (
        get_client,
        fetch_for_you,
        fetch_following,
        fetch_own_tweets,
    )

    client = await get_client()
    username = os.getenv("TWITTER_USERNAME", "unknown")
    now = datetime.now(timezone.utc)

    sources_plan = {
        "for_you":   ["for_you"],
        "following":  ["following"],
        "mine":       ["mine"],
        "all_feeds":  ["for_you", "following"],
        "all":        ["for_you", "following", "mine"],
    }
    plan = sources_plan.get(source)
    if plan is None:
        print(f"ERROR: unknown source '{source}'")
        sys.exit(1)

    seen_ids = _load_seen_ids()
    print(f"Existing tweets in all.jsonl: {len(seen_ids)}\n")

    for kind in plan:
        if kind == "for_you":
            tweets = await fetch_for_you(client, max_tweets)
        elif kind == "following":
            tweets = await fetch_following(client, max_tweets)
        elif kind == "mine":
            tweets = await fetch_own_tweets(client, username, max_tweets)
        else:
            continue

        snapshot_path = _save_snapshot(tweets, kind, now)
        new_count = _append_new_tweets(tweets, seen_ids)

        print(f"  [{kind}] {len(tweets)} fetched  |  {new_count} new  ->  {snapshot_path.relative_to(REPO_ROOT)}")

    total = sum(1 for _ in ALL_JSONL.open()) if ALL_JSONL.exists() else 0
    print(f"\nall.jsonl total: {total} tweets")
    print("Done.")


def main():
    parser = argparse.ArgumentParser(description="Fetch tweets and save them into the repo.")
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
