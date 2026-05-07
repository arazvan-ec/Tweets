#!/usr/bin/env python3
"""
Fetches tweets from X/Twitter using twikit and saves them as JSON files
inside the repo under data/tweets/.

No official API key required — only Twitter credentials (set in .env or
exported as env vars):
  TWITTER_EMAIL, TWITTER_PASSWORD, TWITTER_USERNAME
Or, if you already have a session cookie from a previous run:
  TWITTER_COOKIES_JSON

Each run creates a timestamped snapshot file and updates a cumulative index.
Re-running deduplicates tweets by id so the repo stays lean.

Usage:
  python scripts/save_to_repo.py                     # for_you + following (100 each)
  python scripts/save_to_repo.py --source for_you
  python scripts/save_to_repo.py --source all --max 200
  python scripts/save_to_repo.py --source mine

Sources:
  for_you      — algorithmic "For You" feed
  following    — chronological feed from accounts you follow
  mine         — your own tweets
  all_feeds    — for_you + following (default)
  all          — for_you + following + mine
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

# Repo-relative paths
REPO_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = REPO_ROOT / "data" / "tweets"
SNAPSHOTS_DIR = DATA_DIR / "snapshots"
INDEX_FILE = DATA_DIR / "index.json"
MERGED_FILE = DATA_DIR / "merged.json"


# ---------------------------------------------------------------------------
# Storage helpers
# ---------------------------------------------------------------------------

def _ensure_dirs():
    SNAPSHOTS_DIR.mkdir(parents=True, exist_ok=True)


def _load_index() -> list[dict]:
    if INDEX_FILE.exists():
        try:
            return json.loads(INDEX_FILE.read_text())
        except Exception:
            return []
    return []


def _save_index(entries: list[dict]):
    INDEX_FILE.write_text(json.dumps(entries, indent=2, ensure_ascii=False))


def _load_merged() -> dict[str, dict]:
    """Returns {tweet_id: tweet_dict}."""
    if MERGED_FILE.exists():
        try:
            data = json.loads(MERGED_FILE.read_text())
            return {t["id"]: t for t in data if t.get("id")}
        except Exception:
            return {}
    return {}


def _save_merged(tweets_by_id: dict[str, dict]):
    all_tweets = sorted(
        tweets_by_id.values(),
        key=lambda t: t.get("created_at") or "",
        reverse=True,
    )
    MERGED_FILE.write_text(json.dumps(all_tweets, indent=2, ensure_ascii=False))


def save_snapshot(tweets: list[dict], source: str, username: str) -> Path:
    """Writes a timestamped snapshot file and returns its path."""
    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    filename = f"{ts}_{source}.json"
    path = SNAPSHOTS_DIR / filename

    snapshot = {
        "fetched_at": datetime.now(timezone.utc).isoformat(),
        "source": source,
        "username": username,
        "count": len(tweets),
        "tweets": tweets,
    }
    path.write_text(json.dumps(snapshot, indent=2, ensure_ascii=False))
    print(f"  Snapshot saved: {path.relative_to(REPO_ROOT)}")
    return path


def update_index(snapshot_path: Path, source: str, username: str, count: int):
    entries = _load_index()
    entries.insert(0, {
        "file": str(snapshot_path.relative_to(REPO_ROOT)),
        "fetched_at": datetime.now(timezone.utc).isoformat(),
        "source": source,
        "username": username,
        "count": count,
    })
    _save_index(entries)
    print(f"  Index updated ({len(entries)} snapshots total).")


def merge_tweets(new_tweets: list[dict]):
    merged = _load_merged()
    before = len(merged)
    for t in new_tweets:
        if t.get("id"):
            merged[t["id"]] = t
    _save_merged(merged)
    added = len(merged) - before
    print(f"  Merged file: {len(merged)} unique tweets (+{added} new).")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

async def run(source: str, max_tweets: int):
    # Import here so that the twikit patches in fetch_tweets are applied first.
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

    _ensure_dirs()
    client = await get_client()

    sources_map = {
        "for_you":   [("for_you",)],
        "following": [("following",)],
        "timeline":  [("for_you",)],
        "mine":      [("mine",)],
        "both":      [("for_you",), ("mine",)],
        "all_feeds": [("for_you",), ("following",)],
        "all":       [("for_you",), ("following",), ("mine",)],
    }
    plan = sources_map.get(source)
    if plan is None:
        print(f"ERROR: unknown source '{source}'")
        sys.exit(1)

    for (kind,) in plan:
        if kind == "for_you":
            tweets = await fetch_for_you(client, max_tweets)
        elif kind == "following":
            tweets = await fetch_following(client, max_tweets)
        elif kind == "mine":
            tweets = await fetch_own_tweets(client, username, max_tweets)
        else:
            continue

        if not tweets:
            print(f"  No tweets fetched for source '{kind}'.")
            continue

        snap_path = save_snapshot(tweets, kind, username)
        update_index(snap_path, kind, username, len(tweets))
        merge_tweets(tweets)

    print("\nDone.  Commit data/tweets/ to save to the repo.")
    print(f"  data/tweets/merged.json   — all unique tweets (deduped)")
    print(f"  data/tweets/snapshots/    — individual run snapshots")
    print(f"  data/tweets/index.json    — metadata index of all snapshots")


def main():
    parser = argparse.ArgumentParser(
        description="Fetch tweets and save them as JSON files in the repo."
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
