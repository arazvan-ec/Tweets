#!/usr/bin/env python3
"""
Fetches tweets and saves them as JSONL files in data/tweets/ inside the repo.
No Supabase required — output is plain files you can commit, grep, and analyse.

Files are written to:  data/tweets/YYYY-MM-DD_<source>.jsonl
Each line is one JSON tweet object.  Existing files are deduplicated by tweet id
so running the script multiple times in a day is safe.

Usage:
  python scripts/save_tweets.py                      # for_you + following
  python scripts/save_tweets.py --source for_you
  python scripts/save_tweets.py --source following
  python scripts/save_tweets.py --source mine        # your own tweets
  python scripts/save_tweets.py --source all         # all three
  python scripts/save_tweets.py --max 200
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


# ---------------------------------------------------------------------------
# Re-use auth + fetchers from the existing fetch_tweets module
# ---------------------------------------------------------------------------

sys.path.insert(0, str(REPO_ROOT))
from scripts.fetch_tweets import (
    get_client,
    fetch_for_you,
    fetch_following,
    fetch_own_tweets,
)


# ---------------------------------------------------------------------------
# File helpers
# ---------------------------------------------------------------------------

def _date_str() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def _load_seen_ids(path: Path) -> set[str]:
    if not path.exists():
        return set()
    seen = set()
    with path.open("r", encoding="utf-8") as f:
        for line in f:
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


def save_tweets(tweets: list[dict], source: str) -> tuple[int, Path]:
    """
    Appends new tweets to data/tweets/YYYY-MM-DD_<source>.jsonl.
    Returns (new_count, file_path).
    """
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    path = DATA_DIR / f"{_date_str()}_{source}.jsonl"

    seen_ids = _load_seen_ids(path)
    new_tweets = [t for t in tweets if str(t.get("id", "")) not in seen_ids]

    if new_tweets:
        with path.open("a", encoding="utf-8") as f:
            for t in new_tweets:
                f.write(json.dumps(t, ensure_ascii=False) + "\n")

    return len(new_tweets), path


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

async def run(source: str, max_tweets: int):
    username = os.getenv("TWITTER_USERNAME")
    client = await get_client()

    plan: list[str] = []
    if source in ("for_you", "timeline"):
        plan = ["for_you"]
    elif source == "following":
        plan = ["following"]
    elif source == "mine":
        plan = ["mine"]
    elif source in ("all_feeds", "both"):
        plan = ["for_you", "following"]
    elif source == "all":
        plan = ["for_you", "following", "mine"]
    else:
        print(f"ERROR: unknown source '{source}'")
        sys.exit(1)

    total_new = 0
    for kind in plan:
        if kind == "for_you":
            tweets = await fetch_for_you(client, max_tweets)
        elif kind == "following":
            tweets = await fetch_following(client, max_tweets)
        elif kind == "mine":
            if not username:
                print("ERROR: TWITTER_USERNAME not set — needed for --source mine")
                sys.exit(1)
            tweets = await fetch_own_tweets(client, username, max_tweets)
        else:
            continue

        new_count, path = save_tweets(tweets, kind)
        total_new += new_count
        print(f"  [{kind}] {new_count} new tweets saved → {path.relative_to(REPO_ROOT)}")

    print(f"\nDone. {total_new} new tweets written to data/tweets/")


def main():
    parser = argparse.ArgumentParser(
        description="Fetch tweets and save as JSONL files in data/tweets/."
    )
    parser.add_argument(
        "--source",
        choices=["for_you", "following", "mine", "timeline", "both", "all_feeds", "all"],
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
