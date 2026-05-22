#!/usr/bin/env python3
"""
Fetches tweets and saves them as JSON files in data/tweets/.

Does NOT require Supabase — all data stays in the repo.

Usage:
    python scripts/save_to_repo.py                     # for_you + following
    python scripts/save_to_repo.py --source for_you
    python scripts/save_to_repo.py --source all        # for_you + following + mine
    python scripts/save_to_repo.py --source mine --max 200
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
DATA_DIR = REPO_ROOT / "data" / "tweets"

sys.path.insert(0, str(REPO_ROOT))
from scripts.fetch_tweets import (
    get_client,
    fetch_for_you,
    fetch_following,
    fetch_own_tweets,
)


def save_snapshot(tweets: list[dict], source: str) -> Path:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H-%M")
    filename = f"{source}_{ts}.json"
    out_path = DATA_DIR / filename
    payload = {
        "source": source,
        "fetched_at": datetime.now(timezone.utc).isoformat(),
        "count": len(tweets),
        "tweets": tweets,
    }
    out_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2))
    print(f"  Saved {len(tweets)} tweets → {out_path.relative_to(REPO_ROOT)}")
    return out_path


async def run(source: str, max_tweets: int):
    username = os.getenv("TWITTER_USERNAME")
    if not username:
        print("ERROR: TWITTER_USERNAME not set in .env")
        sys.exit(1)

    client = await get_client()

    plan = {
        "for_you":   ["for_you"],
        "following": ["following"],
        "mine":      ["mine"],
        "all_feeds": ["for_you", "following"],
        "all":       ["for_you", "following", "mine"],
        "timeline":  ["for_you"],   # legacy alias
        "both":      ["for_you", "mine"],
    }.get(source)

    if plan is None:
        print(f"ERROR: unknown source '{source}'")
        sys.exit(1)

    for kind in plan:
        if kind == "for_you":
            tweets = await fetch_for_you(client, max_tweets)
        elif kind == "following":
            tweets = await fetch_following(client, max_tweets)
        elif kind == "mine":
            tweets = await fetch_own_tweets(client, username, max_tweets)
        else:
            continue
        save_snapshot(tweets, kind)

    print("\nDone. Commit data/tweets/ to keep the snapshots in git.")


def main():
    parser = argparse.ArgumentParser(
        description="Fetch tweets and save them as JSON files in data/tweets/."
    )
    parser.add_argument(
        "--source",
        choices=["for_you", "following", "mine", "timeline", "both", "all_feeds", "all"],
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
