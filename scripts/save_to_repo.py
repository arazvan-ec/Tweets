#!/usr/bin/env python3
"""
Fetches tweets via twikit and saves them as JSONL files under data/snapshots/
so they can be committed to git for later analysis.

File layout:
  data/snapshots/YYYY-MM-DD/for_you.jsonl
  data/snapshots/YYYY-MM-DD/following.jsonl
  data/snapshots/YYYY-MM-DD/mine.jsonl  (optional)

Each line is a complete JSON object representing one tweet.
Runs are idempotent: tweets already present (by id) are not re-written.
"""

import argparse
import asyncio
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv

sys.path.insert(0, str(Path(__file__).parent))

from fetch_tweets import fetch_following, fetch_for_you, fetch_own_tweets, get_client

load_dotenv()

SNAPSHOTS_DIR = Path(__file__).parent.parent / "data" / "snapshots"


def load_existing_ids(path: Path) -> set[str]:
    ids: set[str] = set()
    if not path.exists():
        return ids
    with open(path) as f:
        for line in f:
            line = line.strip()
            if line:
                try:
                    ids.add(json.loads(line)["id"])
                except Exception:
                    pass
    return ids


def append_tweets(tweets: list[dict], source: str, date_str: str) -> int:
    """Append new (deduplicated) tweets to the JSONL file for the given source/date."""
    out_dir = SNAPSHOTS_DIR / date_str
    out_dir.mkdir(parents=True, exist_ok=True)
    out_file = out_dir / f"{source}.jsonl"

    existing_ids = load_existing_ids(out_file)

    new_count = 0
    with open(out_file, "a", encoding="utf-8") as f:
        for tweet in tweets:
            if tweet.get("id") and tweet["id"] not in existing_ids:
                f.write(json.dumps(tweet, ensure_ascii=False) + "\n")
                existing_ids.add(tweet["id"])
                new_count += 1

    return new_count


async def run(sources: list[str], max_tweets: int) -> int:
    username = os.getenv("TWITTER_USERNAME") or ""
    client = await get_client()
    date_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    total_new = 0
    for source in sources:
        print(f"\nSource: {source}")
        if source == "for_you":
            tweets = await fetch_for_you(client, max_tweets)
        elif source == "following":
            tweets = await fetch_following(client, max_tweets)
        elif source == "mine":
            tweets = await fetch_own_tweets(client, username, max_tweets)
        else:
            print(f"  Unknown source '{source}', skipping.")
            continue

        new = append_tweets(tweets, source, date_str)
        print(f"  Saved {new} new tweets (fetched {len(tweets)}, duplicates skipped).")
        total_new += new

    print(f"\nTotal new tweets saved: {total_new}")
    return total_new


def main():
    parser = argparse.ArgumentParser(description="Fetch and save tweets to the repo.")
    parser.add_argument(
        "--source",
        choices=["for_you", "following", "mine", "all"],
        default="all",
        help="Which feed(s) to fetch (default: all = for_you + following)",
    )
    parser.add_argument(
        "--max",
        type=int,
        default=100,
        help="Max tweets per source (default: 100)",
    )
    args = parser.parse_args()

    sources = ["for_you", "following"] if args.source == "all" else [args.source]
    asyncio.run(run(sources, args.max))


if __name__ == "__main__":
    main()
