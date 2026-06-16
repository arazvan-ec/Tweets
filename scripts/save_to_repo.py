#!/usr/bin/env python3
"""
Fetches tweets from X/Twitter and saves them as JSON files in the repo:
  data/tweets/YYYY-MM-DD/{source}.json

Each file is a JSON object with a "tweets" list.  Running multiple times
in the same day merges results and deduplicates by tweet ID, keeping the
most recently fetched version of each tweet (so metrics stay fresh).

Usage (from project root):
    python scripts/save_to_repo.py
    python scripts/save_to_repo.py --source for_you --max 200
    python scripts/save_to_repo.py --source all  # for_you + following + mine
"""

import argparse
import asyncio
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

# Allow running as `python scripts/save_to_repo.py` from the project root.
sys.path.insert(0, str(Path(__file__).parent.parent))

from dotenv import load_dotenv
load_dotenv()

from scripts.fetch_tweets import get_client, fetch_for_you, fetch_following, fetch_own_tweets

DATA_DIR = Path(__file__).parent.parent / "data" / "tweets"

SOURCES_MAP = {
    "for_you":   ["for_you"],
    "following": ["following"],
    "timeline":  ["for_you"],       # legacy alias
    "mine":      ["mine"],
    "all_feeds": ["for_you", "following"],
    "all":       ["for_you", "following", "mine"],
}


def _load_existing(path: Path) -> dict[str, dict]:
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return {t["id"]: t for t in data.get("tweets", []) if t.get("id")}
    except Exception:
        return {}


def save_day_file(tweets: list[dict], source: str, date_str: str) -> Path:
    out_dir = DATA_DIR / date_str
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"{source}.json"

    # Merge: existing tweets are overwritten by the freshest fetch for that ID.
    existing = _load_existing(out_path)
    for t in tweets:
        if t.get("id"):
            existing[t["id"]] = t

    merged = sorted(
        existing.values(),
        key=lambda t: t.get("created_at") or "",
        reverse=True,
    )

    payload = {
        "date": date_str,
        "source": source,
        "fetched_at": datetime.now(timezone.utc).isoformat(),
        "count": len(merged),
        "tweets": merged,
    }
    out_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"  Saved {len(merged)} tweets ({len(tweets)} new/updated) → {out_path}")
    return out_path


async def run(source: str, max_tweets: int):
    plan = SOURCES_MAP.get(source)
    if plan is None:
        print(f"ERROR: unknown source '{source}'")
        sys.exit(1)

    username = os.getenv("TWITTER_USERNAME", "")
    date_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    print("Authenticating with Twitter/X…")
    client = await get_client()

    for kind in plan:
        if kind == "for_you":
            tweets = await fetch_for_you(client, max_tweets)
        elif kind == "following":
            tweets = await fetch_following(client, max_tweets)
        elif kind == "mine":
            tweets = await fetch_own_tweets(client, username, max_tweets)
        else:
            continue
        save_day_file(tweets, kind, date_str)

    print("\nDone.")


def main():
    parser = argparse.ArgumentParser(
        description="Fetch tweets and save as JSON files in data/tweets/."
    )
    parser.add_argument(
        "--source",
        choices=list(SOURCES_MAP.keys()),
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
