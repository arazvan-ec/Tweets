#!/usr/bin/env python3
"""
Fetches the home timeline (For You + Following) and appends new tweets to
daily JSONL files at data/tweets/YYYY-MM-DD/{source}.jsonl.

Each line is a self-contained JSON tweet object. Duplicates (same tweet ID)
are skipped, so the script is safe to run multiple times per day.

No Supabase needed — the repo is the storage.
"""

import asyncio
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from scripts.fetch_tweets import get_client, fetch_for_you, fetch_following

REPO_ROOT = Path(__file__).parent.parent
DATA_DIR = REPO_ROOT / "data" / "tweets"
SOURCES = [
    ("for_you", fetch_for_you),
    ("following", fetch_following),
]


def _existing_ids(path: Path) -> set[str]:
    if not path.exists():
        return set()
    ids: set[str] = set()
    with path.open(encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                try:
                    ids.add(json.loads(line)["id"])
                except Exception:
                    pass
    return ids


def _append(path: Path, tweets: list[dict]) -> int:
    existing = _existing_ids(path)
    new = [t for t in tweets if t.get("id") and t["id"] not in existing]
    if not new:
        return 0
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        for t in new:
            f.write(json.dumps(t, ensure_ascii=False) + "\n")
    return len(new)


async def main(max_tweets: int = 100) -> None:
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    day_dir = DATA_DIR / today

    client = await get_client()

    total_new = 0
    for name, fetcher in SOURCES:
        print(f"\nFetching {name}...")
        try:
            tweets = await fetcher(client, max_tweets)
        except Exception as e:
            print(f"  ERROR: {e}", file=sys.stderr)
            continue
        path = day_dir / f"{name}.jsonl"
        added = _append(path, tweets)
        total_new += added
        print(f"  {added} new tweets → {path.relative_to(REPO_ROOT)}")

    print(f"\nTotal new tweets saved: {total_new}")


if __name__ == "__main__":
    import argparse
    p = argparse.ArgumentParser(description="Fetch tweets and save to repo JSONL files.")
    p.add_argument("--max", type=int, default=100, help="Max tweets per source (default: 100)")
    args = p.parse_args()
    asyncio.run(main(args.max))
