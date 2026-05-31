#!/usr/bin/env python3
"""
Fetch tweets from your X/Twitter timeline and save them as JSON files
inside data/tweets/. No Supabase needed — everything stays in this repo.

Run:
    python scripts/save_local.py                  # For You + Following (default)
    python scripts/save_local.py --source mine    # Your own tweets
    python scripts/save_local.py --source all     # All three feeds
    python scripts/save_local.py --max 200        # More tweets per feed
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

SOURCES = {
    "for_you":   ["for_you"],
    "following": ["following"],
    "mine":      ["mine"],
    "all_feeds": ["for_you", "following"],
    "all":       ["for_you", "following", "mine"],
}


def save_snapshot(tweets: list[dict], source: str, timestamp: str) -> Path:
    DATA_DIR.mkdir(parents=True, exist_ok=True)

    payload = {
        "fetched_at": timestamp,
        "source": source,
        "count": len(tweets),
        "tweets": tweets,
    }
    text = json.dumps(payload, ensure_ascii=False, indent=2)

    # Timestamped archive copy
    archive = DATA_DIR / f"{timestamp}_{source}.json"
    archive.write_text(text, encoding="utf-8")

    # "latest" pointer — always reflects the most recent fetch of this feed
    (DATA_DIR / f"latest_{source}.json").write_text(text, encoding="utf-8")

    print(f"  Saved {len(tweets):>4} tweets -> {archive.name}")
    return archive


async def run(source_key: str, max_tweets: int):
    username = os.getenv("TWITTER_USERNAME", "me")
    client = await get_client()
    timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%d_%H-%M-%S")

    for source in SOURCES[source_key]:
        print(f"\n[{source}]")
        if source == "for_you":
            tweets = await fetch_for_you(client, max_tweets)
        elif source == "following":
            tweets = await fetch_following(client, max_tweets)
        elif source == "mine":
            tweets = await fetch_own_tweets(client, username, max_tweets)
        else:
            continue
        save_snapshot(tweets, source, timestamp)

    print(f"\nDone. Files saved to: {DATA_DIR}")


def main():
    parser = argparse.ArgumentParser(
        description="Fetch tweets and save them locally as JSON files in data/tweets/."
    )
    parser.add_argument(
        "--source",
        choices=list(SOURCES),
        default="all_feeds",
        help="Feed to fetch: for_you | following | mine | all_feeds (default) | all",
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
