#!/usr/bin/env python3
"""
Fetches tweets from X/Twitter and saves them as JSONL files in data/tweets/.
One file per calendar day (UTC). Tweets are deduplicated by ID within each file.

Usage:
    python scripts/save_to_files.py [--source all_feeds] [--max 100]

Sources:
    for_you    — Home "For You" algorithmic feed
    following  — Home "Following" chronological feed
    mine       — Your own tweets
    all_feeds  — for_you + following  (default)
    all        — for_you + following + mine
"""

import asyncio
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

# Allow running as `python scripts/save_to_files.py` from the repo root
sys.path.insert(0, str(Path(__file__).parent.parent))

from dotenv import load_dotenv

load_dotenv()

DATA_DIR = Path(__file__).parent.parent / "data" / "tweets"


# ---------------------------------------------------------------------------
# File helpers
# ---------------------------------------------------------------------------

def _load_seen_ids(filepath: Path) -> set:
    seen = set()
    if not filepath.exists():
        return seen
    for line in filepath.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
            tid = obj.get("id")
            if tid:
                seen.add(str(tid))
        except json.JSONDecodeError:
            pass
    return seen


def append_tweets(tweets: list[dict], source: str, date_str: str) -> int:
    """Append new tweets to the day's JSONL file. Returns count of new tweets."""
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    filepath = DATA_DIR / f"{date_str}.jsonl"
    seen = _load_seen_ids(filepath)

    fetched_at = datetime.now(timezone.utc).isoformat()
    new_count = 0
    with filepath.open("a", encoding="utf-8") as f:
        for tweet in tweets:
            tid = str(tweet.get("id") or "")
            if not tid or tid in seen:
                continue
            record = dict(tweet)
            record["_source"] = source
            record["_fetched_at"] = fetched_at
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
            seen.add(tid)
            new_count += 1

    return new_count


def update_index(date_str: str, source_counts: dict[str, int]):
    """Update data/tweets/index.json with the latest fetch metadata."""
    index_path = DATA_DIR / "index.json"
    index: dict = {}
    if index_path.exists():
        try:
            index = json.loads(index_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            pass

    now_iso = datetime.now(timezone.utc).isoformat()
    index["last_fetched_at"] = now_iso
    index["last_date"] = date_str

    fetches: list = index.get("fetches", [])
    fetches.append({
        "date": date_str,
        "fetched_at": now_iso,
        "new_tweets": source_counts,
        "total_new": sum(source_counts.values()),
    })
    index["fetches"] = fetches[-200:]  # keep last 200 entries

    index_path.write_text(
        json.dumps(index, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

async def main():
    import argparse

    parser = argparse.ArgumentParser(description="Fetch tweets and save to JSONL files.")
    parser.add_argument(
        "--source",
        choices=["for_you", "following", "mine", "all_feeds", "all"],
        default="all_feeds",
    )
    parser.add_argument("--max", type=int, default=100, help="Max tweets per source")
    args = parser.parse_args()

    from scripts.fetch_tweets import (
        get_client,
        fetch_for_you,
        fetch_following,
        fetch_own_tweets,
    )

    date_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    username = os.getenv("TWITTER_USERNAME", "unknown")

    print(f"Connecting to Twitter as @{username}...")
    client = await get_client()
    print("Connected.\n")

    source_counts: dict[str, int] = {}

    if args.source in ("for_you", "all_feeds", "all"):
        tweets = await fetch_for_you(client, args.max)
        n = append_tweets(tweets, "for_you", date_str)
        source_counts["for_you"] = n
        print(f"  for_you  : {n} new tweets saved (of {len(tweets)} fetched)")

    if args.source in ("following", "all_feeds", "all"):
        tweets = await fetch_following(client, args.max)
        n = append_tweets(tweets, "following", date_str)
        source_counts["following"] = n
        print(f"  following: {n} new tweets saved (of {len(tweets)} fetched)")

    if args.source in ("mine", "all"):
        tweets = await fetch_own_tweets(client, username, args.max)
        n = append_tweets(tweets, "mine", date_str)
        source_counts["mine"] = n
        print(f"  mine     : {n} new tweets saved (of {len(tweets)} fetched)")

    update_index(date_str, source_counts)
    total = sum(source_counts.values())
    print(f"\nTotal new tweets saved: {total}")
    print(f"Files in: {DATA_DIR}")


if __name__ == "__main__":
    asyncio.run(main())
