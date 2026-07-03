#!/usr/bin/env python3
"""
Fetches the tweets you'd actually see (Home "For You" + "Following", plus
your own tweets) via twikit and writes them as JSON files inside this repo,
under data/archive/. No Supabase and no official X API involved — this is a
separate, lightweight persistence path meant for later offline analysis
(reading, comparing snapshots over time, etc.).

Each run appends one immutable snapshot file per source plus an index.json
listing all snapshots for that source, so nothing gets overwritten and you
can diff/compare across runs.
"""

import argparse
import asyncio
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

from scripts.fetch_tweets import (
    fetch_for_you,
    fetch_following,
    fetch_own_tweets,
    get_client,
)

REPO_ROOT = Path(__file__).parent.parent
DEFAULT_OUT_DIR = REPO_ROOT / "data" / "archive"

SOURCE_PLANS = {
    "for_you": ["for_you"],
    "following": ["following"],
    "mine": ["mine"],
    "all_feeds": ["for_you", "following"],
    "all": ["for_you", "following", "mine"],
}


def save_snapshot(tweets: list[dict], source: str, username: str, out_dir: Path) -> Path:
    now = datetime.now(timezone.utc)
    source_dir = out_dir / source
    source_dir.mkdir(parents=True, exist_ok=True)

    snapshot_name = f"{now.strftime('%Y-%m-%dT%H%M%SZ')}.json"
    snapshot_path = source_dir / snapshot_name
    payload = {
        "source": source,
        "username": username,
        "fetched_at": now.isoformat(),
        "count": len(tweets),
        "tweets": tweets,
    }
    snapshot_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n")

    index_path = source_dir / "index.json"
    index = json.loads(index_path.read_text()) if index_path.exists() else []
    index.append({"file": snapshot_name, "fetched_at": payload["fetched_at"], "count": payload["count"]})
    index_path.write_text(json.dumps(index, ensure_ascii=False, indent=2) + "\n")

    return snapshot_path


async def run(source: str, max_tweets: int, out_dir: Path):
    import os

    username = os.getenv("TWITTER_USERNAME")
    plan = SOURCE_PLANS.get(source)
    if plan is None:
        print(f"ERROR: unknown source '{source}'")
        sys.exit(1)

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
        path = save_snapshot(tweets, kind, username, out_dir)
        print(f"  Saved {len(tweets)} {kind} tweets -> {path.relative_to(REPO_ROOT)}")

    print("\nDone.")


def main():
    parser = argparse.ArgumentParser(description="Fetch tweets and archive them as JSON files in this repo.")
    parser.add_argument(
        "--source",
        choices=list(SOURCE_PLANS.keys()),
        default="all",
        help="Which feed(s) to fetch (default: all = for_you + following + mine)",
    )
    parser.add_argument("--max", type=int, default=100, help="Max tweets per source (default: 100)")
    parser.add_argument(
        "--out-dir",
        type=Path,
        default=DEFAULT_OUT_DIR,
        help="Directory to write snapshots into (default: data/archive)",
    )
    args = parser.parse_args()
    asyncio.run(run(args.source, args.max, args.out_dir))


if __name__ == "__main__":
    main()
