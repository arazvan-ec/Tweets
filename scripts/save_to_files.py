#!/usr/bin/env python3
"""
Fetches tweets from X/Twitter using twikit and saves them as JSON files
in data/tweets/YYYY-MM-DD/ inside the repo.

Run this locally or via GitHub Actions — no Supabase needed.
"""

import argparse
import asyncio
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

# Reuse auth + fetch logic from the existing script.
sys.path.insert(0, str(Path(__file__).parent))
from fetch_tweets import get_client, fetch_for_you, fetch_following, fetch_own_tweets

DATA_DIR = Path(__file__).parent.parent / "data" / "tweets"


def save_snapshot(tweets: list[dict], source: str, run_ts: datetime) -> Path:
    date_str = run_ts.strftime("%Y-%m-%d")
    time_str = run_ts.strftime("%H%M")
    folder = DATA_DIR / date_str
    folder.mkdir(parents=True, exist_ok=True)

    out_path = folder / f"{source}_{time_str}.json"
    payload = {
        "fetched_at": run_ts.isoformat(),
        "source": source,
        "count": len(tweets),
        "tweets": tweets,
    }
    out_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2))
    print(f"  Saved {len(tweets)} tweets -> {out_path.relative_to(Path(__file__).parent.parent)}")

    # Keep a symlink-free "latest" copy for easy access
    latest = DATA_DIR / f"latest_{source}.json"
    latest.write_text(json.dumps(payload, ensure_ascii=False, indent=2))
    return out_path


async def run(source: str, max_tweets: int):
    run_ts = datetime.now(timezone.utc)
    client = await get_client()
    username = os.getenv("TWITTER_USERNAME", "")

    sources_to_run = []
    if source in ("for_you", "timeline", "all_feeds", "all"):
        sources_to_run.append("for_you")
    if source in ("following", "all_feeds", "all"):
        sources_to_run.append("following")
    if source in ("mine", "all"):
        sources_to_run.append("mine")

    for kind in sources_to_run:
        if kind == "for_you":
            tweets = await fetch_for_you(client, max_tweets)
        elif kind == "following":
            tweets = await fetch_following(client, max_tweets)
        elif kind == "mine":
            tweets = await fetch_own_tweets(client, username, max_tweets)
        else:
            continue
        save_snapshot(tweets, kind, run_ts)

    print("\nDone.")


def main():
    parser = argparse.ArgumentParser(description="Fetch tweets and save as JSON files in the repo.")
    parser.add_argument(
        "--source",
        choices=["for_you", "following", "mine", "timeline", "all_feeds", "all"],
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
