#!/usr/bin/env python3
"""
Fetches tweets using twikit and saves them as JSON files inside data/tweets/.

Usage:
    python scripts/save_tweets_local.py                   # for_you + following
    python scripts/save_tweets_local.py --source mine     # only your own tweets
    python scripts/save_tweets_local.py --source all      # all three feeds
    python scripts/save_tweets_local.py --max 200         # more tweets

Output files (one per fetch, never overwrite):
    data/tweets/YYYY-MM-DD_HHMMSS_<source>.json
"""

import argparse
import asyncio
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

# Re-use all the fetch logic and patches from the existing module.
sys.path.insert(0, str(Path(__file__).parent))
from fetch_tweets import get_client, fetch_for_you, fetch_following, fetch_own_tweets

import os
from dotenv import load_dotenv

load_dotenv()

DATA_DIR = Path(__file__).parent.parent / "data" / "tweets"


def save_json(tweets: list[dict], source: str) -> Path:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d_%H%M%S")
    path = DATA_DIR / f"{stamp}_{source}.json"
    path.write_text(json.dumps(tweets, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


async def run(source: str, max_tweets: int):
    username = os.getenv("TWITTER_USERNAME", "unknown")
    client = await get_client()

    sources_map = {
        "for_you":   [("for_you",)],
        "following": [("following",)],
        "mine":      [("mine",)],
        "all_feeds": [("for_you",), ("following",)],
        "all":       [("for_you",), ("following",), ("mine",)],
    }
    plan = sources_map.get(source)
    if plan is None:
        print(f"ERROR: unknown source '{source}'")
        sys.exit(1)

    for (kind,) in plan:
        if kind == "for_you":
            tweets = await fetch_for_you(client, max_tweets)
        elif kind == "following":
            tweets = await fetch_following(client, max_tweets)
        elif kind == "mine":
            tweets = await fetch_own_tweets(client, username, max_tweets)
        else:
            continue

        out = save_json(tweets, kind)
        print(f"  Saved {len(tweets)} {kind} tweets -> {out.relative_to(Path.cwd())}")

    print("\nDone. Commit data/tweets/ to keep the files in the repo.")


def main():
    parser = argparse.ArgumentParser(description="Save tweets as JSON files in data/tweets/.")
    parser.add_argument(
        "--source",
        choices=["for_you", "following", "mine", "all_feeds", "all"],
        default="all_feeds",
        help="Which feed(s) to fetch (default: all_feeds)",
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
