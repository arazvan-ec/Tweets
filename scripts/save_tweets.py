#!/usr/bin/env python3
"""
Fetches your X/Twitter timeline and saves it as JSON files in data/tweets/.
No Supabase required — the output lives directly in the repo.

Usage (from project root):
    python scripts/save_tweets.py                  # for_you + following (default)
    python scripts/save_tweets.py --source for_you
    python scripts/save_tweets.py --source following
    python scripts/save_tweets.py --source mine     # your own tweets
    python scripts/save_tweets.py --source all      # all three feeds
    python scripts/save_tweets.py --max 200         # more tweets per feed

Each run writes one file per feed:
    data/tweets/2026-06-10_14-30-00_for_you.json
    data/tweets/2026-06-10_14-30-00_following.json

Commit those files to keep a searchable, diff-able history in git.
"""

import argparse
import asyncio
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

# Ensure project root is importable regardless of cwd
sys.path.insert(0, str(Path(__file__).parent.parent))

from dotenv import load_dotenv

# Importing fetch_tweets applies the necessary twikit patches at module level
from scripts.fetch_tweets import (
    get_client,
    fetch_for_you,
    fetch_following,
    fetch_own_tweets,
)

load_dotenv()

DATA_DIR = Path(__file__).parent.parent / "data" / "tweets"

SOURCES = {
    "for_you":   [("for_you",)],
    "following": [("following",)],
    "mine":      [("mine",)],
    "all_feeds": [("for_you",), ("following",)],
    "all":       [("for_you",), ("following",), ("mine",)],
}


def save_snapshot(tweets: list[dict], source: str, ts: str) -> Path:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    path = DATA_DIR / f"{ts}_{source}.json"
    path.write_text(json.dumps(tweets, indent=2, ensure_ascii=False))
    print(f"  Saved {len(tweets)} tweets -> {path.relative_to(Path.cwd()) if Path.cwd() in path.parents else path}")
    return path


async def run(source: str, max_tweets: int) -> None:
    username = os.getenv("TWITTER_USERNAME", "")
    plan = SOURCES.get(source)
    if plan is None:
        print(f"ERROR: unknown source '{source}'")
        sys.exit(1)

    client = await get_client()
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d_%H-%M-%S")

    for (kind,) in plan:
        if kind == "for_you":
            tweets = await fetch_for_you(client, max_tweets)
        elif kind == "following":
            tweets = await fetch_following(client, max_tweets)
        elif kind == "mine":
            if not username:
                print("ERROR: TWITTER_USERNAME not set in .env")
                sys.exit(1)
            tweets = await fetch_own_tweets(client, username, max_tweets)
        else:
            continue
        save_snapshot(tweets, kind, ts)

    print("\nDone. Run `git add data/tweets/ && git commit` to save the snapshot.")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Save timeline tweets to local JSON files in data/tweets/."
    )
    parser.add_argument(
        "--source",
        choices=list(SOURCES),
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
