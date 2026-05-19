#!/usr/bin/env python3
"""
Fetches tweets from X/Twitter using twikit and saves them as JSON files
in data/tweets/<YYYY-MM-DD>/ within this repository. No Supabase needed.

Usage:
    python scripts/fetch_and_save.py [--source SOURCE] [--max N]

Sources:
    for_you    — "Para ti" feed (algorithmic)
    following  — "Siguiendo" feed (chronological)
    mine       — Your own tweets
    all_feeds  — for_you + following (default)
    all        — for_you + following + mine

Auth (set in .env or environment):
    TWITTER_EMAIL, TWITTER_PASSWORD, TWITTER_USERNAME
    — or just set TWITTER_COOKIES_JSON with the cookies JSON string.
"""

import argparse
import asyncio
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT))
load_dotenv(_ROOT / ".env")

from scripts.fetch_tweets import (
    get_client,
    fetch_for_you,
    fetch_following,
    fetch_own_tweets,
)

DATA_DIR = _ROOT / "data" / "tweets"

SOURCES = {
    "for_you": ["for_you"],
    "following": ["following"],
    "mine": ["mine"],
    "all_feeds": ["for_you", "following"],
    "all": ["for_you", "following", "mine"],
}


def save_snapshot(tweets: list[dict], source: str, fetched_at: datetime) -> Path:
    date_dir = DATA_DIR / fetched_at.strftime("%Y-%m-%d")
    date_dir.mkdir(parents=True, exist_ok=True)

    out_path = date_dir / f"{fetched_at.strftime('%H%M%S')}_{source}.json"
    out_path.write_text(
        json.dumps(
            {
                "fetched_at": fetched_at.isoformat(),
                "source": source,
                "count": len(tweets),
                "tweets": tweets,
            },
            indent=2,
            ensure_ascii=False,
        )
    )
    print(f"  Saved {len(tweets)} tweets -> {out_path.relative_to(_ROOT)}")
    return out_path


async def run(source: str, max_tweets: int):
    plan = SOURCES.get(source)
    if plan is None:
        print(f"ERROR: unknown source '{source}'. Choose from: {', '.join(SOURCES)}")
        sys.exit(1)

    username = os.getenv("TWITTER_USERNAME", "")
    client = await get_client()
    fetched_at = datetime.now(timezone.utc)

    for kind in plan:
        if kind == "for_you":
            tweets = await fetch_for_you(client, max_tweets)
        elif kind == "following":
            tweets = await fetch_following(client, max_tweets)
        elif kind == "mine":
            if not username:
                print("ERROR: TWITTER_USERNAME required for --source mine")
                sys.exit(1)
            tweets = await fetch_own_tweets(client, username, max_tweets)
        else:
            continue
        save_snapshot(tweets, kind, fetched_at)

    print("\nDone.")
    print("To commit to the repo:")
    print("  git add data/tweets/")
    print(f"  git commit -m 'Fetch tweets {fetched_at.strftime(\"%Y-%m-%d\")}'")
    print("  git push")


def main():
    parser = argparse.ArgumentParser(
        description="Fetch tweets and save them as JSON files in data/tweets/."
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
