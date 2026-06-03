#!/usr/bin/env python3
"""
Fetches tweets from X/Twitter using twikit and saves them as JSON files
directly into the repository under data/tweets/.

No Supabase required — credentials only (TWITTER_EMAIL / PASSWORD / USERNAME
in .env, or TWITTER_COOKIES_JSON for cookie-based auth).

Directory layout after a run:
  data/tweets/
    latest/
      for_you.json          ← most recent For You snapshot
      following.json        ← most recent Following snapshot
    2026-06-03/
      143000_for_you.json   ← timestamped snapshot
      143000_following.json
    index.json              ← list of all snapshots (appended each run)
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
# Helpers
# ---------------------------------------------------------------------------

def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


def _save(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def _load_index() -> list:
    idx_path = DATA_DIR / "index.json"
    if idx_path.exists():
        try:
            return json.loads(idx_path.read_text(encoding="utf-8"))
        except Exception:
            return []
    return []


def _append_index(entry: dict) -> None:
    index = _load_index()
    index.append(entry)
    _save(DATA_DIR / "index.json", index)


# ---------------------------------------------------------------------------
# Core save routine
# ---------------------------------------------------------------------------

def save_snapshot(tweets: list[dict], source: str, fetched_at: str) -> Path:
    """Persists a tweet list and returns the snapshot file path."""
    ts = datetime.fromisoformat(fetched_at)
    date_str = ts.strftime("%Y-%m-%d")
    time_str = ts.strftime("%H%M%S")

    snapshot_path = DATA_DIR / date_str / f"{time_str}_{source}.json"
    latest_path = DATA_DIR / "latest" / f"{source}.json"

    payload = {
        "source": source,
        "fetched_at": fetched_at,
        "count": len(tweets),
        "tweets": tweets,
    }

    _save(snapshot_path, payload)
    _save(latest_path, payload)

    _append_index({
        "source": source,
        "fetched_at": fetched_at,
        "count": len(tweets),
        "file": str(snapshot_path.relative_to(REPO_ROOT)),
    })

    print(f"  Saved {len(tweets)} tweets -> {snapshot_path.relative_to(REPO_ROOT)}")
    return snapshot_path


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

async def run(source: str, max_tweets: int) -> None:
    # Import auth + fetchers from existing script (reuse patches + cookie logic)
    sys.path.insert(0, str(REPO_ROOT))
    from scripts.fetch_tweets import get_client, fetch_for_you, fetch_following, fetch_own_tweets

    username = os.getenv("TWITTER_USERNAME", "")
    fetched_at = _now_utc().isoformat()

    client = await get_client()

    plan: list[str] = []
    if source in ("for_you", "timeline", "all_feeds", "all"):
        plan.append("for_you")
    if source in ("following", "all_feeds", "all"):
        plan.append("following")
    if source in ("mine", "all"):
        plan.append("mine")

    if not plan:
        print(f"ERROR: unknown source '{source}'")
        sys.exit(1)

    for kind in plan:
        if kind == "for_you":
            tweets = await fetch_for_you(client, max_tweets)
        elif kind == "following":
            tweets = await fetch_following(client, max_tweets)
        elif kind == "mine":
            tweets = await fetch_own_tweets(client, username, max_tweets)
        else:
            continue
        save_snapshot(tweets, kind, fetched_at)

    print("\nDone.")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Fetch tweets and save as JSON files in the repo."
    )
    parser.add_argument(
        "--source",
        choices=["for_you", "following", "mine", "timeline", "all_feeds", "all"],
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
