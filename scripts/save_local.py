#!/usr/bin/env python3
"""
Fetches tweets and saves them as JSON files inside data/tweets/.

No Supabase required — everything stays in the repo.

Directory layout:
  data/tweets/YYYY-MM-DD/
    for_you.json      <- For You timeline
    following.json    <- Following timeline
    mine.json         <- Your own tweets
    _index.json       <- Run metadata (date, counts, sources)

Run examples:
  python -m scripts.save_local                        # all feeds (default)
  python -m scripts.save_local --source for_you
  python -m scripts.save_local --source following
  python -m scripts.save_local --source mine
  python -m scripts.save_local --source all --max 200
"""

import argparse
import asyncio
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv

# Make sure project root is on sys.path when running as a script
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from scripts.fetch_tweets import (
    get_client,
    fetch_for_you,
    fetch_following,
    fetch_own_tweets,
)

load_dotenv()

DATA_DIR = ROOT / "data" / "tweets"


def today_dir() -> Path:
    date_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    d = DATA_DIR / date_str
    d.mkdir(parents=True, exist_ok=True)
    return d


def save_json(path: Path, data) -> None:
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"  Saved {len(data)} tweets -> {path.relative_to(ROOT)}")


async def run(source: str, max_tweets: int) -> None:
    username = os.getenv("TWITTER_USERNAME")
    if not username:
        print("ERROR: TWITTER_USERNAME not set in .env")
        sys.exit(1)

    client = await get_client()
    out_dir = today_dir()
    fetched: dict[str, list] = {}

    sources = {
        "for_you":    [("for_you",)],
        "following":  [("following",)],
        "mine":       [("mine",)],
        "all_feeds":  [("for_you",), ("following",)],
        "all":        [("for_you",), ("following",), ("mine",)],
    }
    plan = sources.get(source)
    if plan is None:
        print(f"ERROR: unknown source '{source}'")
        sys.exit(1)

    for (kind,) in plan:
        if kind == "for_you":
            tweets = await fetch_for_you(client, max_tweets)
        elif kind == "following":
            tweets = await fetch_following(client, max_tweets)
        elif kind == "mine":
            tweets = await fetch_own_tweets(client, username, max_tweets)
        else:
            continue

        dest = out_dir / f"{kind}.json"
        # Merge with existing file for the same day (avoids losing earlier runs)
        existing: list = []
        if dest.exists():
            try:
                existing = json.loads(dest.read_text(encoding="utf-8"))
            except Exception:
                existing = []
        seen_ids = {t["id"] for t in existing}
        new_tweets = [t for t in tweets if t["id"] not in seen_ids]
        merged = existing + new_tweets
        save_json(dest, merged)
        fetched[kind] = merged

    # Write / update _index.json
    index_path = out_dir / "_index.json"
    index = {
        "date": out_dir.name,
        "username": username,
        "last_run_utc": datetime.now(timezone.utc).isoformat(),
        "sources": {k: len(v) for k, v in fetched.items()},
    }
    index_path.write_text(json.dumps(index, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"  Index      -> {index_path.relative_to(ROOT)}")
    print("\nDone.")


def main() -> None:
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
