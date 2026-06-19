#!/usr/bin/env python3
"""
Fetches tweets via twikit and appends them to JSONL files under data/tweets/.

Each source gets its own subdirectory and one file per UTC date:
    data/tweets/for_you/2026-06-19.jsonl
    data/tweets/following/2026-06-19.jsonl
    data/tweets/mine/2026-06-19.jsonl

Designed to be run as a scheduled GitHub Actions workflow so the results are
committed directly to the repository for offline analysis.

Usage:
    python scripts/save_to_files.py [--source all_feeds] [--max 100]
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

DATA_DIR = Path(__file__).parent.parent / "data" / "tweets"


def _today_utc() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def _load_seen_ids(path: Path) -> set[str]:
    if not path.exists():
        return set()
    seen: set[str] = set()
    with path.open(encoding="utf-8") as f:
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


def save_tweets(tweets: list[dict], source: str) -> int:
    """Append unseen tweets to data/tweets/<source>/YYYY-MM-DD.jsonl.

    Returns the number of tweets actually written.
    """
    if not tweets:
        return 0

    out_dir = DATA_DIR / source
    out_dir.mkdir(parents=True, exist_ok=True)
    out_file = out_dir / f"{_today_utc()}.jsonl"

    seen = _load_seen_ids(out_file)

    new_tweets = [t for t in tweets if t.get("id") and str(t["id"]) not in seen]
    if not new_tweets:
        print(f"  [{source}] 0 new (all {len(tweets)} already in {out_file.name})")
        return 0

    saved_at = datetime.now(timezone.utc).isoformat()
    with out_file.open("a", encoding="utf-8") as f:
        for t in new_tweets:
            t["_source"] = source
            t["_saved_at"] = saved_at
            f.write(json.dumps(t, ensure_ascii=False) + "\n")

    print(f"  [{source}] +{len(new_tweets)} tweets → {out_file}")
    return len(new_tweets)


async def run(source: str, max_tweets: int) -> None:
    from scripts.fetch_tweets import (
        get_client,
        fetch_for_you,
        fetch_following,
        fetch_own_tweets,
    )

    sources_map: dict[str, list[str]] = {
        "for_you":   ["for_you"],
        "following": ["following"],
        "mine":      ["mine"],
        "all_feeds": ["for_you", "following"],
        "all":       ["for_you", "following", "mine"],
    }

    client = await get_client()
    username = os.getenv("TWITTER_USERNAME", "")

    total = 0
    for kind in sources_map[source]:
        if kind == "for_you":
            tweets = await fetch_for_you(client, max_tweets)
        elif kind == "following":
            tweets = await fetch_following(client, max_tweets)
        elif kind == "mine":
            tweets = await fetch_own_tweets(client, username, max_tweets)
        else:
            continue
        total += save_tweets(tweets, kind)

    print(f"\nDone — {total} new tweet(s) written to {DATA_DIR}/")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Fetch tweets and save them as JSONL files in the repo."
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
