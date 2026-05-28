#!/usr/bin/env python3
"""
Fetches tweets using twikit and saves them as JSON files in data/tweets/.

No Supabase or external database needed — output lives in the repo.

Usage:
    python scripts/save_tweets.py                   # for_you + following
    python scripts/save_tweets.py --source mine     # only your own tweets
    python scripts/save_tweets.py --source all      # all three feeds
    python scripts/save_tweets.py --max 200         # more tweets per source
    python scripts/save_tweets.py --commit          # auto git-commit after saving
"""

import argparse
import asyncio
import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

REPO_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = REPO_ROOT / "data" / "tweets"


def _load_fetch_module():
    """Import fetch helpers from fetch_tweets.py (applies twikit patches too)."""
    sys.path.insert(0, str(REPO_ROOT))
    try:
        from scripts.fetch_tweets import (
            get_client,
            fetch_for_you,
            fetch_following,
            fetch_own_tweets,
        )
        return get_client, fetch_for_you, fetch_following, fetch_own_tweets
    except ImportError as exc:
        print(f"ERROR importing fetch_tweets: {exc}")
        print("Make sure you have run: pip install -r requirements.txt")
        sys.exit(1)


def save_snapshot(tweets: list[dict], source: str, username: str) -> Path:
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d_%H-%M-%S")
    dest_dir = DATA_DIR / source
    dest_dir.mkdir(parents=True, exist_ok=True)
    out_path = dest_dir / f"{ts}.json"
    payload = {
        "fetched_at": datetime.now(timezone.utc).isoformat(),
        "source": source,
        "username": username,
        "count": len(tweets),
        "tweets": tweets,
    }
    out_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2))
    print(f"  Saved {len(tweets)} tweets -> {out_path.relative_to(REPO_ROOT)}")
    return out_path


def git_commit(paths: list[Path], username: str):
    rel_paths = [str(p.relative_to(REPO_ROOT)) for p in paths]
    try:
        subprocess.run(
            ["git", "-C", str(REPO_ROOT), "add"] + rel_paths,
            check=True,
        )
        ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
        msg = f"tweets: snapshot {ts} (@{username})"
        subprocess.run(
            ["git", "-C", str(REPO_ROOT), "commit", "-m", msg],
            check=True,
        )
        print(f"  Committed: {msg}")
    except subprocess.CalledProcessError as e:
        print(f"  git commit failed: {e}")


async def run(source: str, max_tweets: int, auto_commit: bool):
    username = os.getenv("TWITTER_USERNAME", "unknown")
    get_client, fetch_for_you, fetch_following, fetch_own_tweets = _load_fetch_module()

    client = await get_client()

    sources_map = {
        "for_you":   ["for_you"],
        "following": ["following"],
        "mine":      ["mine"],
        "all_feeds": ["for_you", "following"],
        "all":       ["for_you", "following", "mine"],
    }
    plan = sources_map.get(source)
    if plan is None:
        print(f"ERROR: unknown source '{source}'")
        sys.exit(1)

    saved_paths = []
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
        path = save_snapshot(tweets, kind, username)
        saved_paths.append(path)

    if auto_commit and saved_paths:
        print("\n--- git commit ---")
        git_commit(saved_paths, username)

    print("\nDone.")


def main():
    parser = argparse.ArgumentParser(
        description="Fetch tweets and save them as JSON files in the repo."
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
    parser.add_argument(
        "--commit",
        action="store_true",
        help="Auto git-commit the saved files after fetching",
    )
    args = parser.parse_args()
    asyncio.run(run(args.source, args.max, args.commit))


if __name__ == "__main__":
    main()
