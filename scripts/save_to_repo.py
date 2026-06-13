#!/usr/bin/env python3
"""
Fetches tweets using twikit and saves them as JSON files directly in the repo.

No Supabase required — credentials are stored only via your .env or Railway env vars.

Layout written to data/tweets/:
  latest/<source>.json          — always the most recent fetch (overwritten each run)
  <YYYY-MM-DD>/<source>.json    — daily snapshot (overwritten each run that day)

Sources:
  for_you     — "For You" algorithmic feed
  following   — "Following" chronological feed
  mine        — your own tweets
  all         — all three above (default)

Usage:
  python scripts/save_to_repo.py
  python scripts/save_to_repo.py --source for_you --max 200
  python scripts/save_to_repo.py --source mine --max 500

After running, commit the data/tweets/ folder to keep a history:
  git add data/tweets/
  git commit -m "tweets: snapshot $(date +%F)"
  git push
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

REPO_ROOT = Path(__file__).parent.parent
DATA_DIR = REPO_ROOT / "data" / "tweets"


def _write(path: Path, tweets: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(tweets, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"  Saved {len(tweets)} tweets → {path.relative_to(REPO_ROOT)}")


async def run(source: str, max_tweets: int) -> None:
    # Import here so the twikit patches inside fetch_tweets are applied first.
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

    now_utc = datetime.now(timezone.utc)
    date_str = now_utc.strftime("%Y-%m-%d")

    plan = {
        "for_you": ["for_you"],
        "following": ["following"],
        "mine": ["mine"],
        "all": ["for_you", "following", "mine"],
    }.get(source)

    if plan is None:
        print(f"ERROR: unknown source '{source}'. Choose: for_you, following, mine, all")
        sys.exit(1)

    for kind in plan:
        print(f"\nFetching {kind} (max {max_tweets})...")
        if kind == "for_you":
            tweets = await fetch_for_you(client, max_tweets)
        elif kind == "following":
            tweets = await fetch_following(client, max_tweets)
        elif kind == "mine":
            tweets = await fetch_own_tweets(client, username, max_tweets)
        else:
            continue

        _write(DATA_DIR / "latest" / f"{kind}.json", tweets)
        _write(DATA_DIR / date_str / f"{kind}.json", tweets)

    print("\nDone. Commit data/tweets/ to save the snapshot:")
    print("  git add data/tweets/")
    print(f'  git commit -m "tweets: snapshot {date_str}"')
    print("  git push")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Fetch tweets and save them as JSON files in the repo."
    )
    parser.add_argument(
        "--source",
        choices=["for_you", "following", "mine", "all"],
        default="all",
        help="Which feed(s) to fetch (default: all)",
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
