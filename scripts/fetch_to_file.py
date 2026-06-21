#!/usr/bin/env python3
"""
Fetches tweets from X/Twitter using twikit and saves them as JSON files
in data/YYYY-MM-DD/ within the repository. Designed to run from GitHub
Actions so the files are committed and pushed automatically.

Usage:
  python scripts/fetch_to_file.py [--source for_you|following|mine|all] [--max N]
"""

import argparse
import asyncio
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

# Reuse auth, patches and tweet serialization from the existing script.
sys.path.insert(0, str(Path(__file__).parent.parent))
from scripts.fetch_tweets import (
    get_client,
    tweet_to_dict,
    fetch_for_you,
    fetch_following,
    fetch_own_tweets,
)

from dotenv import load_dotenv

load_dotenv()

DATA_DIR = Path(__file__).parent.parent / "data"


def today_dir() -> Path:
    date_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    d = DATA_DIR / date_str
    d.mkdir(parents=True, exist_ok=True)
    return d


def merge_and_save(path: Path, new_tweets: list[dict]) -> tuple[int, int]:
    """
    Merges new_tweets into the existing JSON file at path, deduplicating by id.
    Returns (total_after_merge, newly_added).
    """
    existing: dict[str, dict] = {}
    if path.exists():
        try:
            with path.open() as f:
                for t in json.load(f):
                    if t.get("id"):
                        existing[t["id"]] = t
        except Exception:
            pass

    before = len(existing)
    for t in new_tweets:
        if t.get("id"):
            existing[t["id"]] = t

    sorted_tweets = sorted(
        existing.values(),
        key=lambda t: t.get("created_at") or "",
        reverse=True,
    )
    with path.open("w") as f:
        json.dump(sorted_tweets, f, ensure_ascii=False, indent=2)

    return len(existing), len(existing) - before


async def run(source: str, max_tweets: int):
    username = os.getenv("TWITTER_USERNAME", "")
    client = await get_client()
    today = today_dir()
    fetched_at = datetime.now(timezone.utc).isoformat()

    plan = []
    if source in ("for_you", "all"):
        plan.append("for_you")
    if source in ("following", "all"):
        plan.append("following")
    if source in ("mine", "all"):
        plan.append("mine")

    results_summary = []

    for kind in plan:
        print(f"\n--- {kind} ---")
        if kind == "for_you":
            tweets = await fetch_for_you(client, max_tweets)
        elif kind == "following":
            tweets = await fetch_following(client, max_tweets)
        elif kind == "mine":
            tweets = await fetch_own_tweets(client, username, max_tweets)
        else:
            continue

        out_path = today / f"{kind}.json"
        total, added = merge_and_save(out_path, tweets)
        print(f"  Saved {out_path} — {added} new tweets (total today: {total})")
        results_summary.append({"source": kind, "fetched": len(tweets), "new": added, "total_today": total})

    # Write a small metadata file for the day
    meta_path = today / "_meta.json"
    meta: dict = {}
    if meta_path.exists():
        try:
            with meta_path.open() as f:
                meta = json.load(f)
        except Exception:
            pass
    meta.setdefault("username", username)
    meta["last_fetched_at"] = fetched_at
    meta.setdefault("runs", [])
    meta["runs"].append({"at": fetched_at, "sources": results_summary})
    with meta_path.open("w") as f:
        json.dump(meta, f, ensure_ascii=False, indent=2)

    print(f"\nDone. Data saved in {today}")


def main():
    parser = argparse.ArgumentParser(description="Fetch tweets and save to JSON files in data/.")
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
