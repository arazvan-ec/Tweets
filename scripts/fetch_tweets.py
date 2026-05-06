#!/usr/bin/env python3
"""
Fetches tweets from X/Twitter using twikit (no API key, no browser needed).
Saves results as JSON in data/YYYY-MM-DD/.
"""

import asyncio
import json
import os
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
    user = tweet.user
    return {
        "id": str(tweet.id),
        "text": tweet.text,
        "created_at": tweet.created_at,
        "lang": getattr(tweet, "lang", None),
        "author": {
            "id": str(user.id),
            "name": user.name,
            "username": user.screen_name,
        } if user else None,
        "metrics": {
            "likes": getattr(tweet, "favorite_count", 0) or 0,
            "retweets": getattr(tweet, "retweet_count", 0) or 0,
            "replies": getattr(tweet, "reply_count", 0) or 0,
            "views": getattr(tweet, "view_count", None),
            "quotes": getattr(tweet, "quote_count", 0) or 0,
        },
        "is_retweet": tweet.retweeted_tweet is not None,
        "is_reply": tweet.in_reply_to is not None,
        "is_quote": tweet.quoted_tweet is not None,
        "url": f"https://x.com/{user.screen_name}/status/{tweet.id}" if user else None,
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
