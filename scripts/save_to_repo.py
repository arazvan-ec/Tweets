#!/usr/bin/env python3
"""
Fetches tweets from the home timeline and appends them to JSONL files
in data/tweets/. File layout: data/tweets/YYYY-MM-DD_<source>.jsonl
Each line is a complete tweet JSON object (same schema as tweet_to_dict).
Run from the repo root: python scripts/save_to_repo.py
"""

import asyncio
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

# Allow running from repo root or from scripts/
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.fetch_tweets import (
    get_client,
    fetch_for_you,
    fetch_following,
    fetch_own_tweets,
)

DATA_DIR = Path(__file__).resolve().parent.parent / "data" / "tweets"


def _load_seen_ids(path: Path) -> set[str]:
    if not path.exists():
        return set()
    seen: set[str] = set()
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                try:
                    seen.add(json.loads(line).get("id", ""))
                except Exception:
                    pass
    return seen


def _append_new(path: Path, tweets: list[dict], seen: set[str]) -> int:
    new = [t for t in tweets if t.get("id") and t["id"] not in seen]
    if not new:
        return 0
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", encoding="utf-8") as fh:
        for t in new:
            fh.write(json.dumps(t, ensure_ascii=False) + "\n")
    return len(new)


async def main() -> int:
    source = os.getenv("FETCH_SOURCE", "all_feeds")
    max_tweets = int(os.getenv("FETCH_MAX", "100"))
    username = os.getenv("TWITTER_USERNAME", "")

    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    DATA_DIR.mkdir(parents=True, exist_ok=True)

    client = await get_client()

    plan: list[str] = []
    if source in ("for_you", "timeline", "all_feeds", "all"):
        plan.append("for_you")
    if source in ("following", "all_feeds", "all"):
        plan.append("following")
    if source in ("mine", "both", "all"):
        plan.append("mine")

    total_new = 0
    for kind in plan:
        out_path = DATA_DIR / f"{today}_{kind}.jsonl"
        seen = _load_seen_ids(out_path)

        if kind == "for_you":
            tweets = await fetch_for_you(client, max_tweets)
        elif kind == "following":
            tweets = await fetch_following(client, max_tweets)
        elif kind == "mine":
            if not username:
                print("  mine: TWITTER_USERNAME not set, skipping")
                continue
            tweets = await fetch_own_tweets(client, username, max_tweets)
        else:
            continue

        added = _append_new(out_path, tweets, seen)
        total_new += added
        print(f"  {kind}: +{added} new tweets → {out_path.relative_to(Path.cwd())}")

    print(f"\nTotal new tweets saved: {total_new}")
    return total_new


if __name__ == "__main__":
    result = asyncio.run(main())
    sys.exit(0 if result >= 0 else 1)
