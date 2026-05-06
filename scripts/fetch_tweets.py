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
# Patch: twikit's User.__init__ assumes some legacy fields are always present,
# but X is gradually moving them to other top-level keys (`core`, `privacy`,
# etc.) and dropping the old ones. We replace __init__ with a tolerant
# version that uses .get() everywhere so it works against the live API.
# ---------------------------------------------------------------------------

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

    # If TWITTER_COOKIES_JSON is set (e.g. on Railway / GitHub Actions), write
    # it to disk so twikit can load it. Existing files take priority.
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
    raw_urls = getattr(tweet, "urls", None) or []
    for u in raw_urls:
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

    # Recursively serialize quoted / retweeted (one level only)
    quoted = None
    retweeted = None
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

def save_snapshot(tweets: list[dict], source: str, username: str, supabase_client=None):
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

    if supabase_client is not None and tweets:
        try:
            from sync_to_supabase import sync_snapshot
            meta = {"file": str(rel)}
            sync_snapshot(supabase_client, meta, snapshot)
        except Exception as e:
            print(f"  WARNING: Supabase sync failed: {e}")


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

async def run(source: str, max_tweets: int, push_supabase: bool):
    username = os.getenv("TWITTER_USERNAME")
    client = await get_client()

    supabase_client = None
    if push_supabase:
        sys.path.insert(0, str(Path(__file__).parent))
        from sync_to_supabase import get_client as get_sb_client
        supabase_client = get_sb_client()
        if supabase_client is None:
            print("Note: Supabase not configured (SUPABASE_URL / SUPABASE_KEY missing) — saving locally only.\n")
        else:
            print("Supabase: connected, snapshots will be pushed automatically.\n")

    if source in ("timeline", "both"):
        tweets = await fetch_timeline(client, max_tweets)
        save_snapshot(tweets, "timeline", username, supabase_client)

    if source in ("mine", "both"):
        tweets = await fetch_own_tweets(client, username, max_tweets)
        save_snapshot(tweets, "mine", username, supabase_client)

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
    parser.add_argument(
        "--no-supabase",
        action="store_true",
        help="Skip pushing the snapshot to Supabase (still saves JSON locally).",
    )
    args = parser.parse_args()
    asyncio.run(run(args.source, args.max, push_supabase=not args.no_supabase))


if __name__ == "__main__":
    main()
