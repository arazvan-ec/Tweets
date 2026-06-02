#!/usr/bin/env python3
"""
Fetches tweets (For You + Following timelines, and optionally your own tweets)
and saves them as JSON files inside data/tweets/ so they live in the repo and
can be read, analysed, and compared with any tool.

Directory layout produced:
  data/tweets/
    2026-06-02/
      14-30_for_you.json
      14-30_following.json
    latest_for_you.json      <- always overwritten with the most recent fetch
    latest_following.json

Each file is a JSON array of tweet objects (same schema used by fetch_tweets.py).

Usage:
  python scripts/save_to_repo.py                  # for_you + following
  python scripts/save_to_repo.py --source for_you
  python scripts/save_to_repo.py --source all     # + your own tweets
  python scripts/save_to_repo.py --max 200
"""

import argparse
import asyncio
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

# Re-use auth + fetchers from the existing module (patches included).
sys.path.insert(0, str(Path(__file__).parent.parent))
from scripts.fetch_tweets import (  # noqa: E402
    get_client,
    fetch_for_you,
    fetch_following,
    fetch_own_tweets,
)
from dotenv import load_dotenv

load_dotenv()

DATA_DIR = Path(__file__).parent.parent / "data" / "tweets"


def _save(tweets: list[dict], source: str, now: datetime) -> Path:
    date_dir = DATA_DIR / now.strftime("%Y-%m-%d")
    date_dir.mkdir(parents=True, exist_ok=True)

    timestamped = date_dir / f"{now.strftime('%H-%M')}_{source}.json"
    latest = DATA_DIR / f"latest_{source}.json"

    payload = json.dumps(tweets, ensure_ascii=False, indent=2)
    timestamped.write_text(payload, encoding="utf-8")
    latest.write_text(payload, encoding="utf-8")

    print(f"  Saved {len(tweets)} tweets → {timestamped.relative_to(Path.cwd()) if timestamped.is_relative_to(Path.cwd()) else timestamped}")
    return timestamped


async def run(source: str, max_tweets: int):
    now = datetime.now(timezone.utc)
    client = await get_client()
    username = os.getenv("TWITTER_USERNAME", "me")

    sources = {
        "for_you":   ["for_you"],
        "following": ["following"],
        "mine":      ["mine"],
        "all_feeds": ["for_you", "following"],
        "all":       ["for_you", "following", "mine"],
    }
    plan = sources.get(source)
    if plan is None:
        print(f"ERROR: unknown source '{source}'. Choose from: {', '.join(sources)}")
        sys.exit(1)

    DATA_DIR.mkdir(parents=True, exist_ok=True)

    for kind in plan:
        if kind == "for_you":
            tweets = await fetch_for_you(client, max_tweets)
        elif kind == "following":
            tweets = await fetch_following(client, max_tweets)
        elif kind == "mine":
            tweets = await fetch_own_tweets(client, username, max_tweets)
        else:
            continue
        _save(tweets, kind, now)

    print("\nDone. Commit data/tweets/ to keep a record in the repo.")


def main():
    parser = argparse.ArgumentParser(
        description="Fetch tweets and save them as JSON files in data/tweets/."
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
