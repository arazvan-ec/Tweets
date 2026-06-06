#!/usr/bin/env python3
"""
Fetches tweets and saves them as JSON files in data/YYYY-MM-DD/.

No Supabase needed. Commit the data/ folder to keep a full git history of
your timeline — one folder per day, one JSON file per feed.

Usage:
    python scripts/save_local.py                   # for_you + following (default)
    python scripts/save_local.py --source all      # + your own tweets
    python scripts/save_local.py --source for_you --max 200
"""

import argparse
import asyncio
import json
import os
import sys
from datetime import date
from pathlib import Path

# Allow running as `python scripts/save_local.py` from the repo root.
sys.path.insert(0, str(Path(__file__).parent.parent))

from dotenv import load_dotenv

load_dotenv()

DATA_DIR = Path(__file__).parent.parent / "data"


async def run(source: str, max_tweets: int) -> None:
    from scripts.fetch_tweets import (
        get_client,
        fetch_for_you,
        fetch_following,
        fetch_own_tweets,
    )

    username = os.getenv("TWITTER_USERNAME")
    if not username:
        print("ERROR: TWITTER_USERNAME not set in .env")
        sys.exit(1)

    client = await get_client()

    today = date.today().isoformat()
    out_dir = DATA_DIR / today
    out_dir.mkdir(parents=True, exist_ok=True)

    plan: list[tuple[str, ...]] = {
        "for_you":   [("for_you",)],
        "following": [("following",)],
        "mine":      [("mine",)],
        "all_feeds": [("for_you",), ("following",)],
        "all":       [("for_you",), ("following",), ("mine",)],
    }[source]

    for (kind,) in plan:
        if kind == "for_you":
            tweets = await fetch_for_you(client, max_tweets)
        elif kind == "following":
            tweets = await fetch_following(client, max_tweets)
        elif kind == "mine":
            tweets = await fetch_own_tweets(client, username, max_tweets)
        else:
            continue

        out_file = out_dir / f"{kind}.json"
        with open(out_file, "w", encoding="utf-8") as f:
            json.dump(tweets, f, ensure_ascii=False, indent=2)
        print(f"  Saved {len(tweets)} {kind} tweets -> {out_file.relative_to(DATA_DIR.parent)}")

    print("\nDone.")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Fetch tweets and save as JSON files in data/YYYY-MM-DD/."
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
