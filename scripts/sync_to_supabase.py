#!/usr/bin/env python3
"""
Pushes snapshots stored under data/ to a Supabase Postgres database.

Idempotent: re-running it will upsert rows instead of duplicating them.
Reads SUPABASE_URL and SUPABASE_KEY (anon/publishable) from .env.
"""

import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv
from supabase import create_client, Client

load_dotenv()

ROOT = Path(__file__).parent.parent
DATA_DIR = ROOT / "data"

SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")


def get_client() -> Client:
    if not SUPABASE_URL or not SUPABASE_KEY:
        print("ERROR: set SUPABASE_URL and SUPABASE_KEY in .env")
        sys.exit(1)
    return create_client(SUPABASE_URL, SUPABASE_KEY)


def parse_twitter_date(s: str | None) -> str | None:
    """Twitter dates look like 'Wed May 06 19:34:28 +0000 2026' — convert to ISO."""
    if not s:
        return None
    try:
        dt = datetime.strptime(s, "%a %b %d %H:%M:%S %z %Y")
        return dt.astimezone(timezone.utc).isoformat()
    except Exception:
        return None


def to_int(v):
    if v is None or v == "":
        return None
    try:
        return int(str(v).replace(",", ""))
    except Exception:
        return None


def collect_authors(tweets: list[dict]) -> dict[str, dict]:
    """Returns a deduplicated dict of author rows keyed by id."""
    out: dict[str, dict] = {}
    for t in tweets:
        for source in (t, t.get("quoted_tweet"), t.get("retweeted_tweet")):
            if not source:
                continue
            a = source.get("author")
            if not a or not a.get("id"):
                continue
            out[a["id"]] = {
                "id": a["id"],
                "username": a.get("username"),
                "name": a.get("name"),
                "avatar": a.get("avatar"),
                "verified": bool(a.get("verified", False)),
                "is_blue_verified": bool(a.get("is_blue_verified", False)),
                "last_seen_at": datetime.now(timezone.utc).isoformat(),
            }
    return out


def tweet_row(t: dict) -> dict:
    author = t.get("author") or {}
    metrics = t.get("metrics") or {}
    return {
        "id": t["id"],
        "author_id": author.get("id"),
        "text": t.get("text"),
        "lang": t.get("lang"),
        "created_at": parse_twitter_date(t.get("created_at")),
        "url": t.get("url"),
        "is_retweet": bool(t.get("is_retweet")),
        "is_reply": bool(t.get("is_reply")),
        "is_quote": bool(t.get("is_quote")),
        "in_reply_to_id": t.get("in_reply_to_id"),
        "quoted_tweet_id": (t.get("quoted_tweet") or {}).get("id"),
        "retweeted_tweet_id": (t.get("retweeted_tweet") or {}).get("id"),
        "possibly_sensitive": bool(t.get("possibly_sensitive")),
        "likes": to_int(metrics.get("likes")) or 0,
        "retweets": to_int(metrics.get("retweets")) or 0,
        "replies": to_int(metrics.get("replies")) or 0,
        "quotes": to_int(metrics.get("quotes")) or 0,
        "bookmarks": to_int(metrics.get("bookmarks")) or 0,
        "views": to_int(metrics.get("views")),
        "media": t.get("media") or [],
        "urls": t.get("urls") or [],
        "hashtags": t.get("hashtags") or [],
        "user_mentions": t.get("user_mentions") or [],
        "raw": t,
        "last_seen_at": datetime.now(timezone.utc).isoformat(),
    }


def collect_tweet_rows(tweets: list[dict]) -> dict[str, dict]:
    """Tweets + their nested quote / retweet, deduplicated by id."""
    out: dict[str, dict] = {}
    for t in tweets:
        out[t["id"]] = tweet_row(t)
        for nested in (t.get("quoted_tweet"), t.get("retweeted_tweet")):
            if nested and nested.get("id") and nested["id"] not in out:
                out[nested["id"]] = tweet_row(nested)
    return out


def chunked(items, size: int):
    for i in range(0, len(items), size):
        yield items[i:i + size]


def sync_snapshot(client: Client, snapshot_meta: dict, snapshot_data: dict):
    print(f"  Syncing {snapshot_meta['file']} ({snapshot_data['count']} tweets)...")

    tweets = snapshot_data["tweets"]
    if not tweets:
        return

    # 1) Authors
    authors = list(collect_authors(tweets).values())
    if authors:
        for chunk in chunked(authors, 200):
            client.table("authors").upsert(chunk, on_conflict="id").execute()

    # 2) Tweets (parents first, then children — Postgres FK is to author only,
    #    so order between top-level and nested tweets doesn't matter)
    tweet_rows = list(collect_tweet_rows(tweets).values())
    for chunk in chunked(tweet_rows, 200):
        client.table("tweets").upsert(chunk, on_conflict="id").execute()

    # 3) Snapshot row
    snap = client.table("snapshots").upsert({
        "fetched_at": snapshot_data["fetched_at"],
        "source": snapshot_data["source"],
        "username": snapshot_data["username"],
        "count": snapshot_data["count"],
        "file": snapshot_meta["file"],
    }, on_conflict="file").execute()
    snapshot_id = snap.data[0]["id"]

    # 4) Bridge rows
    bridge = [{"snapshot_id": snapshot_id, "tweet_id": t["id"]} for t in tweets]
    for chunk in chunked(bridge, 500):
        client.table("snapshot_tweets").upsert(chunk).execute()

    print(f"    OK — snapshot id={snapshot_id}")


def main():
    client = get_client()

    index_file = DATA_DIR / "index.json"
    if not index_file.exists():
        print("No data/index.json — nothing to sync.")
        return

    with open(index_file, "r", encoding="utf-8") as f:
        index = json.load(f)

    print(f"Syncing {len(index)} snapshots to Supabase...")
    for meta in index:
        snap_path = ROOT / meta["file"]
        if not snap_path.exists():
            print(f"  Skipping missing file: {meta['file']}")
            continue
        with open(snap_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        sync_snapshot(client, meta, data)

    print("\nDone.")


if __name__ == "__main__":
    main()
