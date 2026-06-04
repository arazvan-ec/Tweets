#!/usr/bin/env python3
"""
Fetches tweets and saves them as JSON files inside data/ so they can be
committed to the repo, diffed, and analysed later without any external database.

Usage:
    python scripts/fetch_local.py                   # for_you + following, 100 each
    python scripts/fetch_local.py --source all      # + your own tweets
    python scripts/fetch_local.py --max 200         # more tweets per feed
    python scripts/fetch_local.py --source for_you  # single feed

Outputs
-------
data/snapshots/YYYYMMDD_HHMMSS_<source>.json   full snapshot for each run/feed
data/tweets.jsonl                               running log of unique tweets (append-only)

Auth
----
Set TWITTER_EMAIL, TWITTER_PASSWORD, TWITTER_USERNAME in a .env file (see
.env.example).  On first run twikit logs in and saves a session cookie to
data/.cookies.json; subsequent runs reuse it.
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

# twikit patches + auth + fetchers live in fetch_tweets.py — reuse them.
# Importing that module applies the monkey-patches twikit needs to work with
# the current X layout, which is exactly what we want here too.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from scripts.fetch_tweets import (  # noqa: E402
    get_client,
    fetch_for_you,
    fetch_following,
    fetch_own_tweets,
)

REPO_ROOT = Path(__file__).resolve().parent.parent
SNAPSHOTS_DIR = REPO_ROOT / "data" / "snapshots"
JSONL_FILE = REPO_ROOT / "data" / "tweets.jsonl"


# ---------------------------------------------------------------------------
# Persistence helpers
# ---------------------------------------------------------------------------

def _load_seen_ids() -> set[str]:
    """Return tweet IDs already written to tweets.jsonl."""
    seen: set[str] = set()
    if JSONL_FILE.exists():
        for line in JSONL_FILE.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                seen.add(json.loads(line)["id"])
            except Exception:
                pass
    return seen


def save_snapshot(tweets: list[dict], source: str, username: str) -> Path:
    """Write a full snapshot JSON file and return its path."""
    SNAPSHOTS_DIR.mkdir(parents=True, exist_ok=True)
    now = datetime.now(timezone.utc)
    fname = f"{now.strftime('%Y%m%d_%H%M%S')}_{source}.json"
    path = SNAPSHOTS_DIR / fname
    payload = {
        "fetched_at": now.isoformat(),
        "source": source,
        "username": username,
        "count": len(tweets),
        "tweets": tweets,
    }
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    rel = path.relative_to(REPO_ROOT)
    print(f"  snapshot -> {rel}  ({len(tweets)} tweets)")
    return path


def append_new_to_jsonl(tweets: list[dict], seen_ids: set[str]) -> int:
    """Append tweets not yet in tweets.jsonl; return count of new ones."""
    JSONL_FILE.parent.mkdir(parents=True, exist_ok=True)
    new_count = 0
    with JSONL_FILE.open("a", encoding="utf-8") as fh:
        for t in tweets:
            tid = t.get("id")
            if not tid or tid in seen_ids:
                continue
            seen_ids.add(tid)
            fh.write(json.dumps(t, ensure_ascii=False) + "\n")
            new_count += 1
    return new_count


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

SOURCES_MAP = {
    "for_you":   ["for_you"],
    "following": ["following"],
    "mine":      ["mine"],
    "all_feeds": ["for_you", "following"],
    "all":       ["for_you", "following", "mine"],
}


async def run(source: str, max_tweets: int) -> None:
    username = os.getenv("TWITTER_USERNAME", "")
    kinds = SOURCES_MAP.get(source)
    if kinds is None:
        print(f"ERROR: unknown source '{source}'")
        sys.exit(1)

    client = await get_client()
    seen_ids = _load_seen_ids()
    total_new = 0

    for kind in kinds:
        print(f"\n--- {kind} ---")
        if kind == "for_you":
            tweets = await fetch_for_you(client, max_tweets)
        elif kind == "following":
            tweets = await fetch_following(client, max_tweets)
        elif kind == "mine":
            tweets = await fetch_own_tweets(client, username, max_tweets)
        else:
            continue

        save_snapshot(tweets, kind, username)
        new = append_new_to_jsonl(tweets, seen_ids)
        total_new += new
        print(f"  +{new} new unique tweets written to tweets.jsonl")

    print(f"\nTotal new tweets added this run: {total_new}")
    print(f"tweets.jsonl now contains ~{len(seen_ids)} unique tweets")
    print("\nNext step: git add data/ && git commit -m 'chore: add tweet snapshot'")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Fetch tweets from X and save them as JSON files in data/."
    )
    parser.add_argument(
        "--source",
        choices=list(SOURCES_MAP),
        default="all_feeds",
        help="Feed(s) to fetch: for_you | following | mine | all_feeds (default) | all",
    )
    parser.add_argument(
        "--max",
        type=int,
        default=100,
        metavar="N",
        help="Max tweets per feed (default: 100)",
    )
    args = parser.parse_args()
    asyncio.run(run(args.source, args.max))


if __name__ == "__main__":
    main()
