#!/usr/bin/env python3
"""
Fetches tweets and saves them as JSON files inside data/tweets/.
No Supabase required — runs standalone with only Twitter credentials.

Usage:
    python scripts/save_tweets.py                     # for_you + following
    python scripts/save_tweets.py --source for_you
    python scripts/save_tweets.py --source following
    python scripts/save_tweets.py --source mine
    python scripts/save_tweets.py --source all
    python scripts/save_tweets.py --max 200

Output files:
    data/tweets/YYYYMMDD_HHMMSS_<source>.json   timestamped snapshot
    data/tweets/latest_<source>.json             always the most recent fetch
"""

import argparse
import asyncio
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv

load_dotenv()

from scripts.fetch_tweets import (
    get_client,
    fetch_for_you,
    fetch_following,
    fetch_own_tweets,
)

DATA_DIR = ROOT / "data" / "tweets"


def save_snapshot(tweets: list[dict], source: str) -> Path:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    now = datetime.now(timezone.utc)
    filename = f"{now.strftime('%Y%m%d_%H%M%S')}_{source}.json"
    path = DATA_DIR / filename
    path.write_text(json.dumps(tweets, ensure_ascii=False, indent=2))
    # Convenience symlink-style file: always points to the latest fetch
    latest = DATA_DIR / f"latest_{source}.json"
    latest.write_text(json.dumps(tweets, ensure_ascii=False, indent=2))
    return path


async def run(source: str, max_tweets: int):
    username = os.getenv("TWITTER_USERNAME", "unknown")
    client = await get_client()

    fetch_plan = []
    if source in ("for_you", "all_feeds", "all"):
        fetch_plan.append("for_you")
    if source in ("following", "all_feeds", "all"):
        fetch_plan.append("following")
    if source in ("mine", "all"):
        fetch_plan.append("mine")

    for kind in fetch_plan:
        if kind == "for_you":
            tweets = await fetch_for_you(client, max_tweets)
        elif kind == "following":
            tweets = await fetch_following(client, max_tweets)
        elif kind == "mine":
            tweets = await fetch_own_tweets(client, username, max_tweets)
        else:
            continue
        path = save_snapshot(tweets, kind)
        print(f"  Saved {len(tweets)} {kind} tweets -> {path.relative_to(ROOT)}")

    print("\nDone.")
    print("Commit the snapshots to persist them in the repo:")
    print("  git add data/tweets/ && git commit -m 'snapshot: <date>'")


def main():
    parser = argparse.ArgumentParser(
        description="Fetch tweets and save as JSON files in data/tweets/."
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
