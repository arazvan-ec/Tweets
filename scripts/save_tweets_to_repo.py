#!/usr/bin/env python3
"""
Fetches tweets from X/Twitter using twikit and saves them as JSON files
directly in the repository under data/tweets/YYYY-MM-DD/.

This script is designed to run via GitHub Actions on a schedule.
It does NOT require the official Twitter/X API — it uses cookie-based auth
via twikit, the same mechanism as a web browser.

Usage:
    python scripts/save_tweets_to_repo.py [--source all_feeds] [--max 50]
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

# Add project root to path so we can reuse fetch_tweets helpers.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.fetch_tweets import get_client, fetch_for_you, fetch_following, fetch_own_tweets

DATA_DIR = Path(__file__).resolve().parent.parent / "data" / "tweets"


def _save(tweets: list[dict], source: str, run_time: datetime) -> Path:
    date_dir = DATA_DIR / run_time.strftime("%Y-%m-%d")
    date_dir.mkdir(parents=True, exist_ok=True)

    filename = f"{source}_{run_time.strftime('%H%M')}.json"
    file_path = date_dir / filename

    payload = {
        "fetched_at": run_time.isoformat(),
        "source": source,
        "count": len(tweets),
        "tweets": tweets,
    }
    file_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2))
    print(f"  Saved {len(tweets)} tweets -> {file_path.relative_to(DATA_DIR.parent.parent)}")

    # Also update latest/<source>.json so callers can always find the freshest copy.
    latest_dir = DATA_DIR / "latest"
    latest_dir.mkdir(parents=True, exist_ok=True)
    (latest_dir / f"{source}.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2)
    )
    return file_path


async def run(source: str, max_tweets: int):
    run_time = datetime.now(timezone.utc)
    print(f"Run started at {run_time.isoformat()}")

    client = await get_client()

    sources_map = {
        "for_you": ["for_you"],
        "following": ["following"],
        "mine": ["mine"],
        "all_feeds": ["for_you", "following"],
        "all": ["for_you", "following", "mine"],
    }
    kinds = sources_map.get(source)
    if kinds is None:
        print(f"ERROR: unknown source '{source}'")
        sys.exit(1)

    username = os.getenv("TWITTER_USERNAME", "unknown")

    for kind in kinds:
        print(f"\nFetching '{kind}'...")
        if kind == "for_you":
            tweets = await fetch_for_you(client, max_tweets)
        elif kind == "following":
            tweets = await fetch_following(client, max_tweets)
        elif kind == "mine":
            tweets = await fetch_own_tweets(client, username, max_tweets)
        else:
            continue
        _save(tweets, kind, run_time)

    print("\nDone.")


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
        default=50,
        help="Max tweets per source (default: 50)",
    )
    args = parser.parse_args()
    asyncio.run(run(args.source, args.max))


if __name__ == "__main__":
    main()
