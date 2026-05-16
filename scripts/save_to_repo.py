#!/usr/bin/env python3
"""
Fetches tweets and saves them as JSON files inside the repo (data/tweets/).

Usage:
    python scripts/save_to_repo.py                  # for_you + following
    python scripts/save_to_repo.py --source all     # for_you + following + mine
    python scripts/save_to_repo.py --source mine    # only your own tweets
    python scripts/save_to_repo.py --max 200        # more tweets per source

Output: data/tweets/YYYY-MM-DD_HHMMSS_<source>.json
Each file is self-contained:
    {
        "fetched_at": "2026-05-16T14:30:00+00:00",
        "source": "for_you",
        "count": 100,
        "tweets": [ ... ]
    }

No Supabase needed — only Twitter credentials in .env.
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


def _output_path(source: str, ts: datetime) -> Path:
    stamp = ts.strftime("%Y-%m-%d_%H%M%S")
    return DATA_DIR / f"{stamp}_{source}.json"


def _save(tweets: list[dict], source: str, ts: datetime) -> Path:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    path = _output_path(source, ts)
    payload = {
        "fetched_at": ts.isoformat(),
        "source": source,
        "count": len(tweets),
        "tweets": tweets,
    }
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


async def run(source: str, max_tweets: int):
    # Import here so the twikit patches in fetch_tweets.py are applied first.
    from scripts.fetch_tweets import (
        fetch_for_you,
        fetch_following,
        fetch_own_tweets,
        get_client,
    )

    username = os.getenv("TWITTER_USERNAME")
    if not username:
        print("ERROR: TWITTER_USERNAME not set in .env")
        sys.exit(1)

    client = await get_client()
    ts = datetime.now(timezone.utc)

    sources_map = {
        "for_you":   [("for_you",)],
        "following":  [("following",)],
        "mine":       [("mine",)],
        "all_feeds":  [("for_you",), ("following",)],
        "all":        [("for_you",), ("following",), ("mine",)],
    }
    plan = sources_map.get(source)
    if plan is None:
        print(f"ERROR: unknown source '{source}'")
        sys.exit(1)

    saved_paths = []
    for (kind,) in plan:
        if kind == "for_you":
            tweets = await fetch_for_you(client, max_tweets)
        elif kind == "following":
            tweets = await fetch_following(client, max_tweets)
        elif kind == "mine":
            tweets = await fetch_own_tweets(client, username, max_tweets)
        else:
            continue

        path = _save(tweets, kind, ts)
        saved_paths.append(path)
        print(f"  Saved {len(tweets)} tweets → {path.relative_to(REPO_ROOT)}")

    print(f"\nDone. {len(saved_paths)} file(s) written.")
    print("Commit & push to persist them in the repo:")
    print("  git add data/tweets/ && git commit -m 'chore: add tweet snapshot' && git push")


def main():
    parser = argparse.ArgumentParser(
        description="Fetch tweets from X and save them as JSON files in data/tweets/."
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
