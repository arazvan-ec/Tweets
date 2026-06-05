#!/usr/bin/env python3
"""
Fetches tweets using twikit and saves them as JSON files inside the repo.

Output layout:
  data/tweets/<YYYY-MM-DD>/<source>_<HH-MM-SS>.json

Each file contains a list of tweet objects with all available fields.
Run this script, then commit the new files to keep a historical record.

Usage:
  python scripts/save_to_repo.py                        # for_you + following (default)
  python scripts/save_to_repo.py --source for_you
  python scripts/save_to_repo.py --source following
  python scripts/save_to_repo.py --source mine
  python scripts/save_to_repo.py --source all           # for_you + following + mine
  python scripts/save_to_repo.py --max 200
"""

import argparse
import asyncio
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

# Allow running from repo root or from scripts/
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv
from scripts.fetch_tweets import (
    get_client,
    fetch_for_you,
    fetch_following,
    fetch_own_tweets,
)

load_dotenv()

DATA_DIR = Path(__file__).resolve().parent.parent / "data" / "tweets"


def save(tweets: list[dict], source: str, timestamp: datetime) -> Path:
    day_dir = DATA_DIR / timestamp.strftime("%Y-%m-%d")
    day_dir.mkdir(parents=True, exist_ok=True)

    filename = f"{source}_{timestamp.strftime('%H-%M-%S')}.json"
    path = day_dir / filename

    path.write_text(
        json.dumps(tweets, ensure_ascii=False, indent=2, default=str),
        encoding="utf-8",
    )
    print(f"  Saved {len(tweets)} tweets -> {path.relative_to(Path(__file__).resolve().parent.parent)}")
    return path


async def run(source: str, max_tweets: int):
    username = os.getenv("TWITTER_USERNAME")
    if not username:
        print("ERROR: TWITTER_USERNAME not set in .env")
        sys.exit(1)

    client = await get_client()
    now = datetime.now(timezone.utc)

    sources = {
        "for_you":   [("for_you",)],
        "following": [("following",)],
        "mine":      [("mine",)],
        "all_feeds": [("for_you",), ("following",)],
        "all":       [("for_you",), ("following",), ("mine",)],
    }
    plan = sources.get(source)
    if plan is None:
        print(f"ERROR: unknown source '{source}'")
        sys.exit(1)

    saved_paths = []
    for (kind,) in plan:
        if kind == "for_you":
            tweets = await fetch_for_you(client, max_tweets)
        elif kind == "following":
            tweets = await fetch_following(client, max_tweets)
        elif kind == "mine":
            tweets = await fetch_own_tweets(client, username, max_tweets)
        else:
            continue
        path = save(tweets, kind, now)
        saved_paths.append(path)

    print(f"\nDone. {len(saved_paths)} file(s) written.")
    print("To commit: git add data/tweets/ && git commit -m 'tweets: <date>'")


def main():
    parser = argparse.ArgumentParser(
        description="Fetch tweets and save them as JSON files in the repo."
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
