#!/usr/bin/env python3
"""
Fetches tweets from the authenticated user's home timeline and their own tweets,
saving everything as JSON files in the data/ directory.
"""

import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

import tweepy
from dotenv import load_dotenv

load_dotenv()

REQUIRED_ENV = [
    "TWITTER_CONSUMER_KEY",
    "TWITTER_CONSUMER_SECRET",
    "TWITTER_ACCESS_TOKEN",
    "TWITTER_ACCESS_TOKEN_SECRET",
    "TWITTER_BEARER_TOKEN",
]

DATA_DIR = Path(__file__).parent.parent / "data"

TWEET_FIELDS = [
    "id", "text", "author_id", "created_at", "lang",
    "public_metrics", "entities", "attachments",
    "in_reply_to_user_id", "referenced_tweets", "possibly_sensitive",
    "context_annotations",
]

USER_FIELDS = ["id", "name", "username", "profile_image_url", "verified", "public_metrics"]

EXPANSIONS = ["author_id", "referenced_tweets.id", "referenced_tweets.id.author_id"]


def check_env():
    missing = [k for k in REQUIRED_ENV if not os.getenv(k)]
    if missing:
        print(f"ERROR: missing env vars: {', '.join(missing)}")
        print("Copy .env.example to .env and fill in your credentials.")
        sys.exit(1)


def build_client():
    return tweepy.Client(
        bearer_token=os.getenv("TWITTER_BEARER_TOKEN"),
        consumer_key=os.getenv("TWITTER_CONSUMER_KEY"),
        consumer_secret=os.getenv("TWITTER_CONSUMER_SECRET"),
        access_token=os.getenv("TWITTER_ACCESS_TOKEN"),
        access_token_secret=os.getenv("TWITTER_ACCESS_TOKEN_SECRET"),
        wait_on_rate_limit=True,
    )


def get_my_user_id(client: tweepy.Client) -> tuple[str, str]:
    me = client.get_me(user_fields=USER_FIELDS)
    return me.data.id, me.data.username


def tweet_to_dict(tweet, users_by_id: dict) -> dict:
    d = {
        "id": str(tweet.id),
        "text": tweet.text,
        "created_at": tweet.created_at.isoformat() if tweet.created_at else None,
        "lang": tweet.lang,
        "author_id": str(tweet.author_id) if tweet.author_id else None,
        "author": users_by_id.get(str(tweet.author_id)),
        "metrics": tweet.public_metrics,
        "entities": tweet.entities,
        "in_reply_to_user_id": str(tweet.in_reply_to_user_id) if tweet.in_reply_to_user_id else None,
        "referenced_tweets": [
            {"type": r.type, "id": str(r.id)} for r in (tweet.referenced_tweets or [])
        ],
        "possibly_sensitive": tweet.possibly_sensitive,
    }
    return d


def extract_users(response) -> dict:
    users_by_id = {}
    if hasattr(response, "includes") and response.includes and "users" in response.includes:
        for u in response.includes["users"]:
            users_by_id[str(u.id)] = {
                "id": str(u.id),
                "name": u.name,
                "username": u.username,
            }
    return users_by_id


def fetch_home_timeline(client: tweepy.Client, max_results: int = 100) -> list[dict]:
    """Fetches recent tweets from the authenticated user's home timeline."""
    print(f"Fetching home timeline (up to {max_results} tweets)...")
    tweets = []
    paginator = tweepy.Paginator(
        client.get_home_timeline,
        tweet_fields=TWEET_FIELDS,
        user_fields=USER_FIELDS,
        expansions=EXPANSIONS,
        max_results=min(max_results, 100),
        limit=max(1, max_results // 100),
    )
    for response in paginator:
        if not response.data:
            break
        users_by_id = extract_users(response)
        for tweet in response.data:
            tweets.append(tweet_to_dict(tweet, users_by_id))
        if len(tweets) >= max_results:
            break
    print(f"  -> {len(tweets)} timeline tweets fetched.")
    return tweets


def fetch_my_tweets(client: tweepy.Client, user_id: str, max_results: int = 100) -> list[dict]:
    """Fetches the authenticated user's own tweets."""
    print(f"Fetching own tweets for user {user_id} (up to {max_results})...")
    tweets = []
    paginator = tweepy.Paginator(
        client.get_users_tweets,
        id=user_id,
        tweet_fields=TWEET_FIELDS,
        user_fields=USER_FIELDS,
        expansions=EXPANSIONS,
        exclude=["retweets"],
        max_results=min(max_results, 100),
        limit=max(1, max_results // 100),
    )
    for response in paginator:
        if not response.data:
            break
        users_by_id = extract_users(response)
        for tweet in response.data:
            tweets.append(tweet_to_dict(tweet, users_by_id))
        if len(tweets) >= max_results:
            break
    print(f"  -> {len(tweets)} own tweets fetched.")
    return tweets


def save_snapshot(tweets: list[dict], source: str, username: str):
    """Saves tweets to data/<date>/<source>_<timestamp>.json and updates the index."""
    now = datetime.now(timezone.utc)
    date_str = now.strftime("%Y-%m-%d")
    timestamp_str = now.strftime("%Y%m%d_%H%M%S")

    day_dir = DATA_DIR / date_str
    day_dir.mkdir(parents=True, exist_ok=True)

    snapshot_file = day_dir / f"{source}_{timestamp_str}.json"
    snapshot = {
        "fetched_at": now.isoformat(),
        "source": source,
        "username": username,
        "count": len(tweets),
        "tweets": tweets,
    }
    with open(snapshot_file, "w", encoding="utf-8") as f:
        json.dump(snapshot, f, ensure_ascii=False, indent=2)
    print(f"  Saved {len(tweets)} tweets -> {snapshot_file.relative_to(DATA_DIR.parent)}")

    update_index(snapshot_file.relative_to(DATA_DIR.parent), snapshot)


def update_index(relative_path: Path, snapshot: dict):
    """Appends an entry to data/index.json for easy browsing."""
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


def main():
    import argparse

    parser = argparse.ArgumentParser(description="Fetch and save tweets from X / Twitter.")
    parser.add_argument(
        "--source",
        choices=["timeline", "mine", "both"],
        default="both",
        help="Which tweets to fetch: home timeline, own tweets, or both (default: both)",
    )
    parser.add_argument(
        "--max",
        type=int,
        default=100,
        help="Max number of tweets per source (default: 100)",
    )
    args = parser.parse_args()

    check_env()
    client = build_client()
    user_id, username = get_my_user_id(client)
    print(f"Authenticated as @{username} (id={user_id})\n")

    if args.source in ("timeline", "both"):
        timeline_tweets = fetch_home_timeline(client, max_results=args.max)
        save_snapshot(timeline_tweets, "timeline", username)

    if args.source in ("mine", "both"):
        my_tweets = fetch_my_tweets(client, user_id, max_results=args.max)
        save_snapshot(my_tweets, "mine", username)

    print("\nDone. See data/ for the saved files.")


if __name__ == "__main__":
    main()
