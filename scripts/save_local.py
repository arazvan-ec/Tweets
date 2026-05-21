#!/usr/bin/env python3
"""
Fetches tweets and saves them to local JSONL files in data/tweets/.
No Supabase required — everything lands in the repo.

Usage:
    python scripts/save_local.py                    # for_you + following (default)
    python scripts/save_local.py --source all       # + your own tweets
    python scripts/save_local.py --source mine      # only your own tweets
    python scripts/save_local.py --max 200          # more tweets per source

Output files:  data/tweets/YYYY-MM-DD_<source>.jsonl
Each line is one tweet as JSON. Reruns on the same day deduplicate by tweet ID.
"""

import argparse
import asyncio
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from dotenv import load_dotenv

from scripts.fetch_tweets import (
    fetch_for_you,
    fetch_following,
    fetch_own_tweets,
    get_client,
)

load_dotenv()

DATA_DIR = Path(__file__).parent.parent / "data" / "tweets"


def _existing_ids(path: Path) -> set:
    if not path.exists():
        return set()
    ids = set()
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                ids.add(json.loads(line)["id"])
            except Exception:
                pass
    return ids


def save_tweets(tweets: list[dict], source: str) -> int:
    """Append new tweets to today's JSONL file, deduped by ID. Returns count added."""
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    path = DATA_DIR / f"{today}_{source}.jsonl"

    existing = _existing_ids(path)
    new = [t for t in tweets if t.get("id") and t["id"] not in existing]

    if new:
        with open(path, "a", encoding="utf-8") as f:
            for t in new:
                f.write(json.dumps(t, ensure_ascii=False) + "\n")

    print(f"  {path.name}: +{len(new)} nuevos ({len(existing)} ya existían)")
    return len(new)


SOURCES = {
    "for_you":   ["for_you"],
    "following": ["following"],
    "mine":      ["mine"],
    "all_feeds": ["for_you", "following"],
    "all":       ["for_you", "following", "mine"],
}


async def run(source: str, max_tweets: int):
    plan = SOURCES.get(source)
    if plan is None:
        print(f"ERROR: fuente desconocida '{source}'")
        sys.exit(1)

    username = os.getenv("TWITTER_USERNAME")
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
        save_tweets(tweets, kind)

    print("\nListo.")


def main():
    parser = argparse.ArgumentParser(
        description="Descarga tweets y los guarda en data/tweets/ como JSONL."
    )
    parser.add_argument(
        "--source",
        choices=list(SOURCES),
        default="all_feeds",
        help="Qué feed(s) capturar (default: all_feeds = for_you + following)",
    )
    parser.add_argument(
        "--max",
        type=int,
        default=100,
        help="Máximo de tweets por fuente (default: 100)",
    )
    args = parser.parse_args()
    asyncio.run(run(args.source, args.max))


if __name__ == "__main__":
    main()
