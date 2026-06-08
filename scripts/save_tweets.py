#!/usr/bin/env python3
"""
Fetches tweets from X/Twitter using twikit and saves them as JSON files
directly in the repository under data/tweets/<YYYY-MM-DD>/<source>.json.

Usage:
    python scripts/save_tweets.py                     # for_you + following
    python scripts/save_tweets.py --source mine       # only your own tweets
    python scripts/save_tweets.py --source all        # all three feeds
    python scripts/save_tweets.py --max 200           # fetch up to 200 per feed

Credentials (set in .env or as environment variables):
    TWITTER_EMAIL      your X login email
    TWITTER_PASSWORD   your X password
    TWITTER_USERNAME   your X handle (without @)

Optionally, export TWITTER_COOKIES_JSON (the contents of data/.cookies.json)
to skip interactive login (useful in CI / Railway).
"""

import argparse
import asyncio
import json
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

import twikit
from dotenv import load_dotenv

load_dotenv()

COOKIES_FILE = Path(__file__).parent.parent / "data" / ".cookies.json"
DATA_DIR = Path(__file__).parent.parent / "data" / "tweets"


# ---------------------------------------------------------------------------
# Patches (same as fetch_tweets.py — keep in sync)
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


from twikit.user import User as _User


def _patched_user_init(self, client, data):
    self._client = client
    legacy = data.get('legacy') or {}
    core = data.get('core') or {}
    privacy = data.get('privacy') or {}
    verification = data.get('verification') or {}
    avatar = data.get('avatar') or {}
    location_obj = data.get('location') or {}
    perspectives = data.get('relationship_perspectives') or {}

    self.id = data.get('rest_id')
    self.created_at = legacy.get('created_at') or core.get('created_at')
    self.name = legacy.get('name') or core.get('name')
    self.screen_name = legacy.get('screen_name') or core.get('screen_name')
    self.profile_image_url = legacy.get('profile_image_url_https') or avatar.get('image_url')
    self.profile_banner_url = legacy.get('profile_banner_url')
    self.url = legacy.get('url')
    self.location = legacy.get('location') or location_obj.get('location')
    self.description = legacy.get('description')
    self.description_urls = legacy.get('entities', {}).get('description', {}).get('urls', [])
    self.urls = legacy.get('entities', {}).get('url', {}).get('urls')
    self.pinned_tweet_ids = legacy.get('pinned_tweet_ids_str', [])
    self.is_blue_verified = data.get('is_blue_verified', False)
    self.verified = legacy.get('verified', False) or verification.get('verified', False)
    self.possibly_sensitive = legacy.get('possibly_sensitive', False)
    self.can_dm = legacy.get('can_dm', False) or privacy.get('can_dm', False)
    self.can_media_tag = legacy.get('can_media_tag', False) or privacy.get('can_media_tag', False)
    self.want_retweets = legacy.get('want_retweets', False) or perspectives.get('want_retweets', False)
    self.default_profile = legacy.get('default_profile', False)
    self.default_profile_image = legacy.get('default_profile_image', False)
    self.has_custom_timelines = legacy.get('has_custom_timelines', False)
    self.followers_count = legacy.get('followers_count', 0)
    self.fast_followers_count = legacy.get('fast_followers_count', 0)
    self.normal_followers_count = legacy.get('normal_followers_count', 0)
    self.following_count = legacy.get('friends_count', 0)
    self.favourites_count = legacy.get('favourites_count', 0)
    self.listed_count = legacy.get('listed_count', 0)
    self.media_count = legacy.get('media_count', 0)
    self.statuses_count = legacy.get('statuses_count', 0)
    self.is_translator = legacy.get('is_translator', False)
    self.translator_type = legacy.get('translator_type')
    self.withheld_in_countries = legacy.get('withheld_in_countries', [])
    self.protected = legacy.get('protected', False) or privacy.get('protected', False)


_User.__init__ = _patched_user_init


# ---------------------------------------------------------------------------
# Auth
# ---------------------------------------------------------------------------

async def get_client() -> twikit.Client:
    client = twikit.Client(language="en-US")

    if not COOKIES_FILE.exists() and os.getenv("TWITTER_COOKIES_JSON"):
        COOKIES_FILE.parent.mkdir(parents=True, exist_ok=True)
        COOKIES_FILE.write_text(os.environ["TWITTER_COOKIES_JSON"])

    if COOKIES_FILE.exists():
        client.load_cookies(str(COOKIES_FILE))
        print("Session loaded from cookies.")
        return client

    email = os.getenv("TWITTER_EMAIL")
    password = os.getenv("TWITTER_PASSWORD")
    username = os.getenv("TWITTER_USERNAME")

    if not email or not password or not username:
        print("ERROR: no cookies and TWITTER_EMAIL/PASSWORD/USERNAME not set in .env")
        sys.exit(1)

    print(f"Logging in as @{username}...")
    await client.login(auth_info_1=email, auth_info_2=username, password=password)
    COOKIES_FILE.parent.mkdir(parents=True, exist_ok=True)
    client.save_cookies(str(COOKIES_FILE))
    print("Login successful, cookies saved.")
    return client


# ---------------------------------------------------------------------------
# Tweet serialization
# ---------------------------------------------------------------------------

def _media_to_dict(m) -> dict:
    return {
        "id": getattr(m, "id", None),
        "type": getattr(m, "type", None),
        "media_url": getattr(m, "media_url", None),
        "expanded_url": getattr(m, "expanded_url", None),
        "display_url": getattr(m, "display_url", None),
        "url": getattr(m, "url", None),
        "width": (getattr(m, "_data", {}) or {}).get("original_info", {}).get("width"),
        "height": (getattr(m, "_data", {}) or {}).get("original_info", {}).get("height"),
    }


def _user_to_dict(user) -> dict | None:
    if not user:
        return None
    return {
        "id": str(getattr(user, "id", "") or ""),
        "name": getattr(user, "name", None),
        "username": getattr(user, "screen_name", None),
        "avatar": getattr(user, "profile_image_url", None),
        "verified": bool(getattr(user, "verified", False)),
        "is_blue_verified": bool(getattr(user, "is_blue_verified", False)),
    }


def tweet_to_dict(tweet, depth: int = 0) -> dict:
    user = getattr(tweet, "user", None)
    media_list = [_media_to_dict(m) for m in (getattr(tweet, "media", []) or [])]

    urls_list = []
    for u in (getattr(tweet, "urls", None) or []):
        if isinstance(u, dict):
            urls_list.append({
                "url": u.get("url"),
                "expanded_url": u.get("expanded_url"),
                "display_url": u.get("display_url"),
            })

    user_mentions = []
    legacy = getattr(tweet, "_legacy", {}) or {}
    for um in legacy.get("entities", {}).get("user_mentions", []) or []:
        user_mentions.append({
            "id": um.get("id_str"),
            "name": um.get("name"),
            "username": um.get("screen_name"),
        })

    hashtags = list(getattr(tweet, "hashtags", []) or [])

    quoted = retweeted = None
    if depth < 1:
        q = getattr(tweet, "quote", None)
        if q is not None:
            quoted = tweet_to_dict(q, depth=depth + 1)
        rt = getattr(tweet, "retweeted_tweet", None)
        if rt is not None:
            retweeted = tweet_to_dict(rt, depth=depth + 1)

    return {
        "id": str(getattr(tweet, "id", "") or ""),
        "text": getattr(tweet, "full_text", None) or getattr(tweet, "text", "") or "",
        "created_at": getattr(tweet, "created_at", None),
        "lang": getattr(tweet, "lang", None),
        "author": _user_to_dict(user),
        "metrics": {
            "likes": getattr(tweet, "favorite_count", 0) or 0,
            "retweets": getattr(tweet, "retweet_count", 0) or 0,
            "replies": getattr(tweet, "reply_count", 0) or 0,
            "views": getattr(tweet, "view_count", None),
            "quotes": getattr(tweet, "quote_count", 0) or 0,
            "bookmarks": getattr(tweet, "bookmark_count", 0) or 0,
        },
        "viewer_state": {
            "liked": bool(legacy.get("favorited", False)),
            "retweeted": bool(legacy.get("retweeted", False)),
            "bookmarked": bool(getattr(tweet, "bookmarked", False) or legacy.get("bookmarked", False)),
        },
        "is_retweet": retweeted is not None,
        "is_reply": getattr(tweet, "in_reply_to", None) is not None,
        "is_quote": getattr(tweet, "is_quote_status", False) or quoted is not None,
        "in_reply_to_id": getattr(tweet, "in_reply_to", None),
        "url": f"https://x.com/{user.screen_name}/status/{tweet.id}" if user and getattr(user, "screen_name", None) else None,
        "media": media_list,
        "urls": urls_list,
        "hashtags": hashtags,
        "user_mentions": user_mentions,
        "quoted_tweet": quoted,
        "retweeted_tweet": retweeted,
        "possibly_sensitive": getattr(tweet, "possibly_sensitive", False) or False,
    }


# ---------------------------------------------------------------------------
# Fetchers
# ---------------------------------------------------------------------------

async def fetch_feed(client: twikit.Client, kind: str, username: str, max_tweets: int) -> list[dict]:
    print(f"Fetching '{kind}' (target: {max_tweets} tweets)...")
    tweets = []

    if kind == "for_you":
        results = await client.get_timeline(count=20)
    elif kind == "following":
        results = await client.get_latest_timeline(count=20)
    elif kind == "mine":
        user = await client.get_user_by_screen_name(username)
        results = await user.get_tweets("Tweets", count=20)
    else:
        raise ValueError(f"Unknown feed kind: {kind}")

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

    print(f"  -> {len(tweets)} tweets.")
    return tweets[:max_tweets]


# ---------------------------------------------------------------------------
# Persistence: save to repo as JSON
# ---------------------------------------------------------------------------

def save_to_repo(tweets: list[dict], source: str, fetched_at: datetime) -> Path:
    date_str = fetched_at.strftime("%Y-%m-%d")
    time_str = fetched_at.strftime("%H%M%S")
    out_dir = DATA_DIR / date_str
    out_dir.mkdir(parents=True, exist_ok=True)

    out_file = out_dir / f"{source}_{time_str}.json"
    payload = {
        "meta": {
            "source": source,
            "fetched_at": fetched_at.isoformat(),
            "count": len(tweets),
        },
        "tweets": tweets,
    }
    out_file.write_text(json.dumps(payload, ensure_ascii=False, indent=2))
    print(f"  Saved {len(tweets)} tweets -> {out_file.relative_to(Path(__file__).parent.parent)}")
    return out_file


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

SOURCES = {
    "for_you":   ["for_you"],
    "following": ["following"],
    "mine":      ["mine"],
    "all_feeds": ["for_you", "following"],
    "all":       ["for_you", "following", "mine"],
}


async def run(source: str, max_tweets: int):
    username = os.getenv("TWITTER_USERNAME", "")
    feeds = SOURCES.get(source)
    if feeds is None:
        print(f"ERROR: unknown source '{source}'")
        sys.exit(1)

    client = await get_client()
    fetched_at = datetime.now(timezone.utc)

    saved_files = []
    for kind in feeds:
        tweets = await fetch_feed(client, kind, username, max_tweets)
        path = save_to_repo(tweets, kind, fetched_at)
        saved_files.append(path)

    print(f"\nDone. {len(saved_files)} file(s) written under data/tweets/")


def main():
    parser = argparse.ArgumentParser(
        description="Fetch tweets from X and save them as JSON files in the repo."
    )
    parser.add_argument(
        "--source",
        choices=list(SOURCES.keys()),
        default="all_feeds",
        help="Which feed(s) to fetch. Default: all_feeds (for_you + following)",
    )
    parser.add_argument(
        "--max",
        type=int,
        default=100,
        help="Max tweets per feed. Default: 100",
    )
    args = parser.parse_args()
    asyncio.run(run(args.source, args.max))


if __name__ == "__main__":
    main()
