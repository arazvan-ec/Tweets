#!/usr/bin/env python3
"""
Fetches tweets via twikit and saves them as JSON files under data/tweets/
so they can be committed to the git repo for offline analysis.

Each run creates one file per source:
  data/tweets/YYYY-MM-DD/HHMMSS_<source>.json

Usage:
  python scripts/save_to_repo.py                    # for_you + following
  python scripts/save_to_repo.py --source all       # + own tweets
  python scripts/save_to_repo.py --source for_you --max 200
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

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data" / "tweets"

sys.path.insert(0, str(ROOT))
from scripts.fetch_tweets import (
    get_client,
    fetch_for_you,
    fetch_following,
    fetch_own_tweets,
)

SOURCES_MAP = {
    "for_you":   ["for_you"],
    "following":  ["following"],
    "mine":       ["mine"],
    "all_feeds":  ["for_you", "following"],
    "all":        ["for_you", "following", "mine"],
}


def save_tweets(tweets: list[dict], source: str, run_at: datetime) -> Path:
    date_dir = DATA_DIR / run_at.strftime("%Y-%m-%d")
    date_dir.mkdir(parents=True, exist_ok=True)
    out_path = date_dir / f"{run_at.strftime('%H%M%S')}_{source}.json"

    payload = {
        "fetched_at": run_at.isoformat(),
        "source": source,
        "count": len(tweets),
        "tweets": tweets,
    }
    out_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2))
    print(f"  Saved {len(tweets)} tweets -> {out_path.relative_to(ROOT)}")
    return out_path


async def run(sources: list[str], max_tweets: int) -> list[Path]:
    username = os.getenv("TWITTER_USERNAME")
    run_at = datetime.now(timezone.utc)

    client = await get_client()

    saved: list[Path] = []
    for source in sources:
        if source == "for_you":
            tweets = await fetch_for_you(client, max_tweets)
        elif source == "following":
            tweets = await fetch_following(client, max_tweets)
        elif source == "mine":
            if not username:
                print("WARNING: TWITTER_USERNAME not set, skipping 'mine'")
                continue
            tweets = await fetch_own_tweets(client, username, max_tweets)
        else:
            continue
        saved.append(save_tweets(tweets, source, run_at))

    print(f"\nDone — {len(saved)} file(s) written.")
    return saved


def main():
    parser = argparse.ArgumentParser(
        description="Fetch tweets and save them as JSON files in data/tweets/."
    )
    parser.add_argument(
        "--source",
        choices=list(SOURCES_MAP),
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
    asyncio.run(run(SOURCES_MAP[args.source], args.max))


if __name__ == "__main__":
    main()
