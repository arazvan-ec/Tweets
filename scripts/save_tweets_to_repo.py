#!/usr/bin/env python3
"""
Fetches tweets and appends them as JSONL files under data/tweets/<source>/<date>.jsonl.
Designed to run from GitHub Actions, which then commits the files back to the repo.

Usage:
    python scripts/save_tweets_to_repo.py [--source all_feeds] [--max 100]
"""

import argparse
import asyncio
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from fetch_tweets import get_client, fetch_for_you, fetch_following, fetch_own_tweets

DATA_DIR = Path(__file__).parent.parent / "data" / "tweets"


async def save_source(client, kind: str, max_tweets: int, now: datetime, username: str):
    if kind == "for_you":
        tweets = await fetch_for_you(client, max_tweets)
    elif kind == "following":
        tweets = await fetch_following(client, max_tweets)
    elif kind == "mine":
        tweets = await fetch_own_tweets(client, username, max_tweets)
    else:
        return

    date_str = now.strftime("%Y-%m-%d")
    out_dir = DATA_DIR / kind
    out_dir.mkdir(parents=True, exist_ok=True)
    out_file = out_dir / f"{date_str}.jsonl"

    existing_ids: set[str] = set()
    if out_file.exists():
        with out_file.open() as f:
            for line in f:
                try:
                    obj = json.loads(line)
                    if obj.get("id"):
                        existing_ids.add(obj["id"])
                except Exception:
                    pass

    new_tweets = [t for t in tweets if t.get("id") not in existing_ids]
    if not new_tweets:
        print(f"  [{kind}] No new tweets on {date_str}.")
        return

    fetched_at = now.isoformat()
    with out_file.open("a", encoding="utf-8") as f:
        for t in new_tweets:
            t["_fetched_at"] = fetched_at
            f.write(json.dumps(t, ensure_ascii=False) + "\n")

    print(f"  [{kind}] Saved {len(new_tweets)} new tweets → {out_file}")


async def run(source: str, max_tweets: int):
    now = datetime.now(timezone.utc)
    username = os.getenv("TWITTER_USERNAME", "")

    client = await get_client()

    kind_map = {
        "for_you":   ["for_you"],
        "following": ["following"],
        "mine":      ["mine"],
        "timeline":  ["for_you"],
        "both":      ["for_you", "mine"],
        "all_feeds": ["for_you", "following"],
        "all":       ["for_you", "following", "mine"],
    }
    kinds = kind_map.get(source)
    if kinds is None:
        print(f"ERROR: unknown source '{source}'")
        sys.exit(1)

    for kind in kinds:
        await save_source(client, kind, max_tweets, now, username)

    print("Done.")


def main():
    parser = argparse.ArgumentParser(description="Save tweets as JSONL files in the repo.")
    parser.add_argument(
        "--source",
        choices=["for_you", "following", "mine", "timeline", "both", "all_feeds", "all"],
        default="all_feeds",
    )
    parser.add_argument("--max", type=int, default=100)
    args = parser.parse_args()
    asyncio.run(run(args.source, args.max))


if __name__ == "__main__":
    main()
