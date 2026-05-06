#!/usr/bin/env python3
"""
Fetches tweets from X/Twitter using twikit (no API key, no browser needed).
Saves results as JSON in data/YYYY-MM-DD/.
"""

import asyncio
import json
import os
import re
import sys
import argparse
from datetime import datetime, timezone
from pathlib import Path

import twikit
from dotenv import load_dotenv

load_dotenv()

DATA_DIR = Path(__file__).parent.parent / "data"
COOKIES_FILE = DATA_DIR / ".cookies.json"


# ---------------------------------------------------------------------------
# Patch: twikit 2.3.3's regex for finding the ondemand.s file hash is broken
# because X changed the HTML layout. We override get_indices to use webpack's
# chunk-id -> hash map directly.
# ---------------------------------------------------------------------------

_INDICES_REGEX = re.compile(r"""(\(\w{1}\[(\d{1,2})\],\s*16\))+""", flags=(re.VERBOSE | re.MULTILINE))


async def _patched_get_indices(self, home_page_response, session, headers):
    body = str(home_page_response)
    chunk_match = re.search(r'(\d+):"ondemand\.s"', body)
    if not chunk_match:
        raise Exception("Couldn't locate ondemand.s chunk id in home page")
    chunk_id = chunk_match.group(1)
    hash_match = re.search(rf'{chunk_id}:"([a-f0-9]+)"', body)
    if not hash_match:
        raise Exception(f"Couldn't locate hash for chunk {chunk_id}")
    hash_val = hash_match.group(1)
    url = f"https://abs.twimg.com/responsive-web/client-web/ondemand.s.{hash_val}a.js"
    js_resp = await session.request(method="GET", url=url, headers=headers)
    indices = [m.group(2) for m in _INDICES_REGEX.finditer(js_resp.text)]
    if not indices:
        raise Exception("Couldn't extract KEY_BYTE indices from ondemand.s")
    indices = list(map(int, indices))
    return indices[0], indices[1:]


from twikit.x_client_transaction.transaction import ClientTransaction
ClientTransaction.get_indices = _patched_get_indices


# ---------------------------------------------------------------------------
# Auth
# ---------------------------------------------------------------------------

async def get_client() -> twikit.Client:
    client = twikit.Client(language="en-US")

    if COOKIES_FILE.exists():
        client.load_cookies(str(COOKIES_FILE))
        print("Session loaded from cookies.")
        return client

    email = os.getenv("TWITTER_EMAIL")
    password = os.getenv("TWITTER_PASSWORD")
    username = os.getenv("TWITTER_USERNAME")

    if not email or not password or not username:
        print("ERROR: set TWITTER_EMAIL, TWITTER_PASSWORD, TWITTER_USERNAME in .env")
        sys.exit(1)

    print(f"Logging in as @{username}...")
    await client.login(auth_info_1=email, auth_info_2=username, password=password)
    COOKIES_FILE.parent.mkdir(parents=True, exist_ok=True)
    client.save_cookies(str(COOKIES_FILE))
    print("Login successful, session saved.")
    return client


# ---------------------------------------------------------------------------
# Tweet serialization
# ---------------------------------------------------------------------------

def tweet_to_dict(tweet) -> dict:
    user = getattr(tweet, "user", None)
    return {
        "id": str(getattr(tweet, "id", "")),
        "text": getattr(tweet, "text", "") or getattr(tweet, "full_text", ""),
        "created_at": getattr(tweet, "created_at", None),
        "lang": getattr(tweet, "lang", None),
        "author": {
            "id": str(getattr(user, "id", "")),
            "name": getattr(user, "name", None),
            "username": getattr(user, "screen_name", None),
        } if user else None,
        "metrics": {
            "likes": getattr(tweet, "favorite_count", 0) or 0,
            "retweets": getattr(tweet, "retweet_count", 0) or 0,
            "replies": getattr(tweet, "reply_count", 0) or 0,
            "views": getattr(tweet, "view_count", None),
            "quotes": getattr(tweet, "quote_count", 0) or 0,
            "bookmarks": getattr(tweet, "bookmark_count", 0) or 0,
        },
        "is_retweet": getattr(tweet, "retweeted_tweet", None) is not None,
        "is_reply": getattr(tweet, "in_reply_to", None) is not None,
        "is_quote": getattr(tweet, "is_quote_status", False),
        "url": f"https://x.com/{user.screen_name}/status/{tweet.id}" if user and getattr(user, "screen_name", None) else None,
    }


# ---------------------------------------------------------------------------
# Fetchers
# ---------------------------------------------------------------------------

async def fetch_timeline(client: twikit.Client, max_tweets: int = 100) -> list[dict]:
    print(f"Fetching home timeline (target: {max_tweets})...")
    tweets = []
    results = await client.get_timeline(count=20)

    while results and len(tweets) < max_tweets:
        for t in results:
            tweets.append(tweet_to_dict(t))
        print(f"  {len(tweets)} collected...")
        if len(tweets) >= max_tweets:
            break
        try:
            results = await results.next()
        except Exception:
            break

    print(f"  -> {len(tweets)} timeline tweets fetched.")
    return tweets[:max_tweets]


async def fetch_own_tweets(client: twikit.Client, username: str, max_tweets: int = 100) -> list[dict]:
    print(f"Fetching @{username}'s tweets (target: {max_tweets})...")
    user = await client.get_user_by_screen_name(username)
    tweets = []
    results = await user.get_tweets("Tweets", count=20)

    while results and len(tweets) < max_tweets:
        for t in results:
            tweets.append(tweet_to_dict(t))
        print(f"  {len(tweets)} collected...")
        if len(tweets) >= max_tweets:
            break
        try:
            results = await results.next()
        except Exception:
            break

    print(f"  -> {len(tweets)} own tweets fetched.")
    return tweets[:max_tweets]


# ---------------------------------------------------------------------------
# Storage
# ---------------------------------------------------------------------------

def save_snapshot(tweets: list[dict], source: str, username: str):
    now = datetime.now(timezone.utc)
    date_str = now.strftime("%Y-%m-%d")
    ts_str = now.strftime("%Y%m%d_%H%M%S")

    day_dir = DATA_DIR / date_str
    day_dir.mkdir(parents=True, exist_ok=True)

    snapshot_file = day_dir / f"{source}_{ts_str}.json"
    snapshot = {
        "fetched_at": now.isoformat(),
        "source": source,
        "username": username,
        "count": len(tweets),
        "tweets": tweets,
    }
    with open(snapshot_file, "w", encoding="utf-8") as f:
        json.dump(snapshot, f, ensure_ascii=False, indent=2)

    rel = snapshot_file.relative_to(DATA_DIR.parent)
    print(f"  Saved {len(tweets)} tweets -> {rel}")
    _update_index(rel, snapshot)


def _update_index(relative_path: Path, snapshot: dict):
    index_file = DATA_DIR / "index.json"
    index = []
    if index_file.exists():
        with open(index_file, "r", encoding="utf-8") as f:
            index = json.load(f)
    index.append({
        "file": str(relative_path),
        "fetched_at": snapshot["fetched_at"],
        "source": snapshot["source"],
        "username": snapshot["username"],
        "count": snapshot["count"],
    })
    with open(index_file, "w", encoding="utf-8") as f:
        json.dump(index, f, ensure_ascii=False, indent=2)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

async def run(source: str, max_tweets: int):
    username = os.getenv("TWITTER_USERNAME")
    client = await get_client()

    if source in ("timeline", "both"):
        tweets = await fetch_timeline(client, max_tweets)
        save_snapshot(tweets, "timeline", username)

    if source in ("mine", "both"):
        tweets = await fetch_own_tweets(client, username, max_tweets)
        save_snapshot(tweets, "mine", username)

    print("\nDone. See data/ for the saved files.")


def main():
    parser = argparse.ArgumentParser(description="Fetch tweets via twikit (no API key needed).")
    parser.add_argument(
        "--source",
        choices=["timeline", "mine", "both"],
        default="both",
        help="Which tweets to fetch (default: both)",
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
