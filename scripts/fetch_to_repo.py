#!/usr/bin/env python3
"""
Fetches tweets and saves them as JSON files directly in the repository.
No Supabase required — only Twitter credentials (via .env or cookies).

Usage:
  python scripts/fetch_to_repo.py                  # for_you + following (default)
  python scripts/fetch_to_repo.py --source for_you
  python scripts/fetch_to_repo.py --source following
  python scripts/fetch_to_repo.py --source mine     # your own tweets
  python scripts/fetch_to_repo.py --source all      # for_you + following + mine
  python scripts/fetch_to_repo.py --max 200         # more tweets per source

Output structure:
  data/feeds/for_you/YYYY-MM-DD.json      ← daily snapshot (merged if run multiple times)
  data/feeds/following/YYYY-MM-DD.json
  data/archive.jsonl                      ← deduplicated append-only archive of all tweets

After fetching, commit and push to keep the history in git:
  git add data/
  git commit -m "tweets $(date +%Y-%m-%d)"
  git push
"""

import argparse
import asyncio
import json
import os
import sys
from datetime import date, datetime, timezone
from pathlib import Path

from dotenv import load_dotenv

sys.path.insert(0, str(Path(__file__).parent.parent))
from scripts.fetch_tweets import (
    get_client,
    fetch_for_you,
    fetch_following,
    fetch_own_tweets,
)

load_dotenv()

REPO_ROOT = Path(__file__).parent.parent
DATA_DIR = REPO_ROOT / "data"
FEEDS_DIR = DATA_DIR / "feeds"
ARCHIVE_FILE = DATA_DIR / "archive.jsonl"


def load_archived_ids() -> set[str]:
    """Returns the set of tweet IDs already present in archive.jsonl."""
    if not ARCHIVE_FILE.exists():
        return set()
    ids: set[str] = set()
    with ARCHIVE_FILE.open(encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
                tid = obj.get("id")
                if tid:
                    ids.add(str(tid))
            except json.JSONDecodeError:
                pass
    return ids


def append_new_to_archive(tweets: list[dict], known_ids: set[str]) -> int:
    """Appends tweets not yet in archive.jsonl. Returns count of new entries."""
    ARCHIVE_FILE.parent.mkdir(parents=True, exist_ok=True)
    added = 0
    with ARCHIVE_FILE.open("a", encoding="utf-8") as f:
        for t in tweets:
            tid = str(t.get("id") or "")
            if not tid or tid in known_ids:
                continue
            f.write(json.dumps(t, ensure_ascii=False) + "\n")
            known_ids.add(tid)
            added += 1
    return added


def save_daily_snapshot(tweets: list[dict], source: str, day: date) -> Path:
    """
    Writes (or merges) the daily snapshot file for a given source.
    If a file for today already exists, incoming tweets are merged in —
    the more-recent fetch wins for any duplicate tweet ID.
    """
    dest = FEEDS_DIR / source / f"{day.isoformat()}.json"
    dest.parent.mkdir(parents=True, exist_ok=True)

    existing: dict[str, dict] = {}
    if dest.exists():
        try:
            payload = json.loads(dest.read_text(encoding="utf-8"))
            for t in payload.get("tweets", []):
                if t.get("id"):
                    existing[str(t["id"])] = t
        except Exception:
            pass

    for t in tweets:
        tid = str(t.get("id") or "")
        if tid:
            existing[tid] = t  # latest fetch wins (fresher metrics)

    merged = sorted(
        existing.values(),
        key=lambda t: t.get("created_at") or "",
        reverse=True,
    )

    dest.write_text(
        json.dumps(
            {
                "source": source,
                "date": day.isoformat(),
                "fetched_at": datetime.now(timezone.utc).isoformat(),
                "count": len(merged),
                "tweets": merged,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    return dest


async def run(sources: list[str], max_tweets: int) -> None:
    username = os.getenv("TWITTER_USERNAME", "")
    today = date.today()
    known_ids = load_archived_ids()
    print(f"Archive: {len(known_ids)} tweets already stored.\n")

    client = await get_client()

    for source in sources:
        if source == "for_you":
            tweets = await fetch_for_you(client, max_tweets)
        elif source == "following":
            tweets = await fetch_following(client, max_tweets)
        elif source == "mine":
            if not username:
                print("ERROR: TWITTER_USERNAME not set — skipping 'mine'")
                continue
            tweets = await fetch_own_tweets(client, username, max_tweets)
        else:
            print(f"Unknown source '{source}', skipping.")
            continue

        dest = save_daily_snapshot(tweets, source, today)
        new = append_new_to_archive(tweets, known_ids)
        rel = dest.relative_to(REPO_ROOT)
        print(f"  {len(tweets)} fetched  |  {new} new → archive  |  snapshot → {rel}\n")

    print("Done.")
    print()
    print("To save in git:")
    print("  git add data/")
    print(f'  git commit -m "tweets {today.isoformat()}"')
    print("  git push")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Fetch tweets and save as JSON files in the repo."
    )
    parser.add_argument(
        "--source",
        choices=["for_you", "following", "mine", "all"],
        default="all",
        help="Which feed(s) to fetch (default: all = for_you + following + mine)",
    )
    parser.add_argument(
        "--max",
        type=int,
        default=100,
        help="Max tweets per source (default: 100)",
    )
    args = parser.parse_args()

    if args.source == "all":
        sources = ["for_you", "following", "mine"]
    else:
        sources = [args.source]

    asyncio.run(run(sources, args.max))


if __name__ == "__main__":
    main()
