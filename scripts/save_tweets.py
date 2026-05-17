#!/usr/bin/env python3
"""
Fetches tweets and saves them as JSON files inside the repo under data/tweets/.

No Supabase needed. Just set TWITTER_EMAIL / TWITTER_PASSWORD / TWITTER_USERNAME
in .env (or as env vars) and run:

    python scripts/save_tweets.py                   # for_you + following
    python scripts/save_tweets.py --source mine     # your own tweets
    python scripts/save_tweets.py --source all      # all three feeds
    python scripts/save_tweets.py --max 200         # more tweets per feed

Output layout:
    data/tweets/YYYY-MM-DD/HH-MM-SS_<source>.json   snapshot files
    data/tweets/index.json                           metadata for all snapshots
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

# Import auth + fetch helpers (and the twikit patches) from the existing module.
sys.path.insert(0, str(REPO_ROOT))
from scripts.fetch_tweets import (  # noqa: E402
    get_client,
    fetch_for_you,
    fetch_following,
    fetch_own_tweets,
)


# ---------------------------------------------------------------------------
# Snapshot helpers
# ---------------------------------------------------------------------------

def _index_path() -> Path:
    return DATA_DIR / "index.json"


def _load_index() -> list[dict]:
    p = _index_path()
    if p.exists():
        try:
            return json.loads(p.read_text())
        except Exception:
            pass
    return []


def _save_index(entries: list[dict]):
    _index_path().write_text(json.dumps(entries, indent=2, ensure_ascii=False))


def save_snapshot(tweets: list[dict], source: str) -> Path:
    now = datetime.now(timezone.utc)
    day_dir = DATA_DIR / now.strftime("%Y-%m-%d")
    day_dir.mkdir(parents=True, exist_ok=True)

    filename = f"{now.strftime('%H-%M-%S')}_{source}.json"
    out_path = day_dir / filename

    out_path.write_text(json.dumps(tweets, indent=2, ensure_ascii=False))

    # Update index
    entries = _load_index()
    entries.append({
        "file": str(out_path.relative_to(REPO_ROOT)),
        "source": source,
        "fetched_at": now.isoformat(),
        "count": len(tweets),
    })
    _save_index(entries)

    return out_path


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

async def run(source: str, max_tweets: int):
    username = os.getenv("TWITTER_USERNAME", "")
    client = await get_client()

    sources_to_fetch: list[str] = []
    if source in ("for_you", "timeline"):
        sources_to_fetch = ["for_you"]
    elif source == "following":
        sources_to_fetch = ["following"]
    elif source == "mine":
        sources_to_fetch = ["mine"]
    elif source in ("all_feeds", "both"):
        sources_to_fetch = ["for_you", "following"]
    elif source == "all":
        sources_to_fetch = ["for_you", "following", "mine"]
    else:
        print(f"ERROR: unknown source '{source}'")
        sys.exit(1)

    for kind in sources_to_fetch:
        if kind == "for_you":
            tweets = await fetch_for_you(client, max_tweets)
        elif kind == "following":
            tweets = await fetch_following(client, max_tweets)
        elif kind == "mine":
            if not username:
                print("ERROR: TWITTER_USERNAME required for --source mine")
                sys.exit(1)
            tweets = await fetch_own_tweets(client, username, max_tweets)
        else:
            continue

        path = save_snapshot(tweets, kind)
        print(f"  Saved {len(tweets)} {kind} tweets -> {path.relative_to(REPO_ROOT)}")

    print("\nDone. Commit data/tweets/ to keep a record in the repo.")


def main():
    parser = argparse.ArgumentParser(
        description="Fetch tweets and save as JSON files in data/tweets/."
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
