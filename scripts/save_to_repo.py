#!/usr/bin/env python3
"""
Fetches tweets from X/Twitter using twikit and saves them as JSON files
directly in the repository under data/YYYY-MM-DD/.

Does not require Supabase. Credentials come from .env or environment variables:
  TWITTER_EMAIL, TWITTER_PASSWORD, TWITTER_USERNAME
  (or TWITTER_COOKIES_JSON for a pre-authenticated session)

Usage:
    python scripts/save_to_repo.py [--source all_feeds] [--max 100] [--date 2026-06-28]
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
DATA_DIR = ROOT / "data"

# Ensure project root is on the path so imports from scripts/ work
sys.path.insert(0, str(ROOT))


async def run(source: str, max_tweets: int, date_str: str):
    from scripts.fetch_tweets import (
        get_client,
        fetch_for_you,
        fetch_following,
        fetch_own_tweets,
    )

    client = await get_client()
    username = os.getenv("TWITTER_USERNAME", "unknown")

    sources_map = {
        "for_you":   [("for_you",   fetch_for_you)],
        "following": [("following", fetch_following)],
        "mine":      [("mine",      None)],
        "all_feeds": [("for_you",   fetch_for_you), ("following", fetch_following)],
        "all":       [("for_you",   fetch_for_you), ("following", fetch_following), ("mine", None)],
    }

    plan = sources_map.get(source)
    if plan is None:
        print(f"ERROR: unknown source '{source}'")
        sys.exit(1)

    out_dir = DATA_DIR / date_str
    out_dir.mkdir(parents=True, exist_ok=True)

    results = {}
    for kind, fetcher in plan:
        if kind == "mine":
            tweets = await fetch_own_tweets(client, username, max_tweets)
        else:
            tweets = await fetcher(client, max_tweets)

        out_file = out_dir / f"{kind}.json"
        with open(out_file, "w", encoding="utf-8") as f:
            json.dump(tweets, f, ensure_ascii=False, indent=2)
        print(f"  Saved {len(tweets)} {kind} tweets -> {out_file.relative_to(ROOT)}")
        results[kind] = len(tweets)

    summary = {
        "fetched_at": datetime.now(timezone.utc).isoformat(),
        "date": date_str,
        "username": username,
        "source": source,
        "max_per_feed": max_tweets,
        "counts": results,
    }
    summary_file = out_dir / "summary.json"
    summary_file.write_text(json.dumps(summary, ensure_ascii=False, indent=2))

    print(f"\nDone. Data saved to data/{date_str}/")
    print(f"  summary: {summary_file.relative_to(ROOT)}")


def main():
    parser = argparse.ArgumentParser(
        description="Fetch tweets and save as JSON files in the repo."
    )
    parser.add_argument(
        "--source",
        choices=["for_you", "following", "mine", "all_feeds", "all"],
        default="all_feeds",
        help="Which feed(s) to fetch (default: all_feeds = for You + following)",
    )
    parser.add_argument(
        "--max",
        type=int,
        default=100,
        help="Max tweets per source (default: 100)",
    )
    parser.add_argument(
        "--date",
        default=None,
        help="Date subfolder name (default: today in UTC, YYYY-MM-DD)",
    )
    args = parser.parse_args()

    date_str = args.date or datetime.now(timezone.utc).strftime("%Y-%m-%d")
    asyncio.run(run(args.source, args.max, date_str))


if __name__ == "__main__":
    main()
