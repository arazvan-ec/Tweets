#!/usr/bin/env python3
"""
Fetches tweets from X/Twitter using twikit and stores everything directly in
Supabase. The only thing that touches local disk is the session cookie file.
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
from supabase import Client, create_client

load_dotenv()

# Local cookie file (session token, not tweet data). Created on first run from
# TWITTER_COOKIES_JSON if missing, refreshed by twikit on subsequent runs.
COOKIES_FILE = Path(__file__).parent.parent / "data" / ".cookies.json"

DATA_DIR = Path(__file__).parent.parent / "data"


# ---------------------------------------------------------------------------
# Patch: twikit's regex for finding the ondemand.s file hash is broken
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
# but X is gradually moving them to other top-level keys. Replace with a
# tolerant version that uses .get() everywhere.
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

    # Hydrate cookies from env var (Railway / GitHub Actions) when no file.
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

async def fetch_for_you(client: twikit.Client, max_tweets: int = 100) -> list[dict]:
    print(f"Fetching home For You timeline (target: {max_tweets})...")
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
    print(f"  -> {len(tweets)} for_you tweets fetched.")
    return tweets[:max_tweets]


async def fetch_following(client: twikit.Client, max_tweets: int = 100) -> list[dict]:
    print(f"Fetching home Following timeline (target: {max_tweets})...")
    tweets = []
    results = await client.get_latest_timeline(count=20)
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
    print(f"  -> {len(tweets)} following tweets fetched.")
    return tweets[:max_tweets]


# Backwards-compatible alias used by older callers (--source timeline).
async def fetch_timeline(client: twikit.Client, max_tweets: int = 100) -> list[dict]:
    return await fetch_for_you(client, max_tweets)


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


async def fetch_replies(client: twikit.Client, tweet_id: str, max_replies: int = 80) -> list[dict]:
    """Fetches direct replies to a single tweet via X's tweet detail endpoint."""
    parent = await client.get_tweet_by_id(tweet_id)
    replies: list[dict] = []
    page = parent.replies
    while page and len(replies) < max_replies:
        for r in page:
            replies.append(tweet_to_dict(r))
        if len(replies) >= max_replies:
            break
        try:
            page = await page.next()
        except Exception:
            break
    return replies[:max_replies]


# ---------------------------------------------------------------------------
# Supabase
# ---------------------------------------------------------------------------

def supabase_client() -> Client | None:
    url = os.getenv("SUPABASE_URL")
    key = os.getenv("SUPABASE_KEY")
    if not url or not key:
        return None
    return create_client(url, key)


def _to_int(v):
    if v is None or v == "":
        return None
    try:
        return int(str(v).replace(",", ""))
    except Exception:
        return None


def _parse_twitter_date(s):
    if not s:
        return None
    try:
        dt = datetime.strptime(s, "%a %b %d %H:%M:%S %z %Y")
        return dt.astimezone(timezone.utc).isoformat()
    except Exception:
        return None


def _author_row(author: dict, now_iso: str) -> dict:
    return {
        "id": author["id"],
        "username": author.get("username"),
        "name": author.get("name"),
        "avatar": author.get("avatar"),
        "verified": bool(author.get("verified", False)),
        "is_blue_verified": bool(author.get("is_blue_verified", False)),
        "last_seen_at": now_iso,
    }


def _tweet_row(t: dict, now_iso: str) -> dict:
    author = t.get("author") or {}
    metrics = t.get("metrics") or {}
    return {
        "id": t["id"],
        "author_id": author.get("id"),
        "text": t.get("text"),
        "lang": t.get("lang"),
        "created_at": _parse_twitter_date(t.get("created_at")),
        "url": t.get("url"),
        "is_retweet": bool(t.get("is_retweet")),
        "is_reply": bool(t.get("is_reply")),
        "is_quote": bool(t.get("is_quote")),
        "in_reply_to_id": t.get("in_reply_to_id"),
        "quoted_tweet_id": (t.get("quoted_tweet") or {}).get("id"),
        "retweeted_tweet_id": (t.get("retweeted_tweet") or {}).get("id"),
        "possibly_sensitive": bool(t.get("possibly_sensitive")),
        "likes": _to_int(metrics.get("likes")) or 0,
        "retweets": _to_int(metrics.get("retweets")) or 0,
        "replies": _to_int(metrics.get("replies")) or 0,
        "quotes": _to_int(metrics.get("quotes")) or 0,
        "bookmarks": _to_int(metrics.get("bookmarks")) or 0,
        "views": _to_int(metrics.get("views")),
        "media": t.get("media") or [],
        "urls": t.get("urls") or [],
        "hashtags": t.get("hashtags") or [],
        "user_mentions": t.get("user_mentions") or [],
        "raw": t,
        "last_seen_at": now_iso,
    }


def _chunked(items, size):
    for i in range(0, len(items), size):
        yield items[i:i + size]


# ---------------------------------------------------------------------------
# File storage
# ---------------------------------------------------------------------------

def save_to_file(tweets: list[dict], source: str, username: str) -> Path:
    """Saves tweets as a JSON file under data/YYYY-MM-DD/<source>.json.

    Each day's file is overwritten with the latest fetch so diffs across
    commits show what changed in your feed day-to-day.
    """
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    out_dir = DATA_DIR / today
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"{source}.json"

    payload = {
        "fetched_at": datetime.now(timezone.utc).isoformat(),
        "source": source,
        "username": username,
        "count": len(tweets),
        "tweets": tweets,
    }
    out_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2))
    print(f"  Saved {len(tweets)} tweets -> {out_path.relative_to(DATA_DIR.parent)}")
    return out_path


def push_tweets_only(sb: Client | None, tweets: list[dict]):
    """Upserts authors + tweets without creating a snapshot (used for replies)."""
    if not tweets or sb is None:
        return
    now_iso = datetime.now(timezone.utc).isoformat()

    authors: dict[str, dict] = {}
    for t in tweets:
        for src in (t, t.get("quoted_tweet"), t.get("retweeted_tweet")):
            if not src:
                continue
            a = src.get("author")
            if a and a.get("id"):
                authors[a["id"]] = _author_row(a, now_iso)
    if authors:
        for chunk in _chunked(list(authors.values()), 200):
            sb.table("authors").upsert(chunk, on_conflict="id").execute()

    rows: dict[str, dict] = {}
    for t in tweets:
        rows[t["id"]] = _tweet_row(t, now_iso)
        for nested in (t.get("quoted_tweet"), t.get("retweeted_tweet")):
            if nested and nested.get("id") and nested["id"] not in rows:
                rows[nested["id"]] = _tweet_row(nested, now_iso)
    for chunk in _chunked(list(rows.values()), 200):
        sb.table("tweets").upsert(chunk, on_conflict="id").execute()


def push_snapshot(sb: Client | None, tweets: list[dict], source: str, username: str):
    if not tweets:
        print(f"  No {source} tweets to push.")
        return
    if sb is None:
        return

    now_iso = datetime.now(timezone.utc).isoformat()
    print(f"  Pushing {len(tweets)} {source} tweets to Supabase...")

    # Authors (top-level + nested), deduplicated
    authors: dict[str, dict] = {}
    for t in tweets:
        for src in (t, t.get("quoted_tweet"), t.get("retweeted_tweet")):
            if not src:
                continue
            a = src.get("author")
            if a and a.get("id"):
                authors[a["id"]] = _author_row(a, now_iso)
    if authors:
        for chunk in _chunked(list(authors.values()), 200):
            sb.table("authors").upsert(chunk, on_conflict="id").execute()

    # Tweets (top-level + nested)
    rows: dict[str, dict] = {}
    for t in tweets:
        rows[t["id"]] = _tweet_row(t, now_iso)
        for nested in (t.get("quoted_tweet"), t.get("retweeted_tweet")):
            if nested and nested.get("id") and nested["id"] not in rows:
                rows[nested["id"]] = _tweet_row(nested, now_iso)
    for chunk in _chunked(list(rows.values()), 200):
        sb.table("tweets").upsert(chunk, on_conflict="id").execute()

    # Snapshot row
    snap = sb.table("snapshots").insert({
        "fetched_at": now_iso,
        "source": source,
        "username": username,
        "count": len(tweets),
    }).execute()
    snapshot_id = snap.data[0]["id"]

    # Bridge rows
    bridge = [{"snapshot_id": snapshot_id, "tweet_id": t["id"]} for t in tweets]
    for chunk in _chunked(bridge, 500):
        sb.table("snapshot_tweets").upsert(chunk).execute()

    print(f"    OK — snapshot id={snapshot_id}")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

async def run(source: str, max_tweets: int, save_files: bool = True):
    username = os.getenv("TWITTER_USERNAME")

    sb = supabase_client()
    if sb:
        print("Supabase: connected.")
    else:
        print("Supabase: not configured — skipping DB push.")
    print()

    client = await get_client()

    sources = {
        "for_you": [("for_you",)],
        "following": [("following",)],
        "timeline": [("for_you",)],          # legacy alias
        "mine": [("mine",)],
        "both": [("for_you",), ("mine",)],   # legacy
        "all_feeds": [("for_you",), ("following",)],
        "all": [("for_you",), ("following",), ("mine",)],
    }
    plan = sources.get(source)
    if plan is None:
        print(f"ERROR: unknown source '{source}'")
        sys.exit(1)

    for (kind,) in plan:
        if kind == "for_you":
            tweets = await fetch_for_you(client, max_tweets)
        elif kind == "following":
            tweets = await fetch_following(client, max_tweets)
        elif kind == "mine":
            tweets = await fetch_own_tweets(client, username, max_tweets)
        else:
            continue
        if save_files:
            save_to_file(tweets, kind, username)
        if sb:
            push_snapshot(sb, tweets, kind, username)

    print("\nDone.")


def main():
    parser = argparse.ArgumentParser(
        description="Fetch tweets and save them as JSON files in data/ (and optionally to Supabase)."
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
    parser.add_argument(
        "--no-files",
        action="store_true",
        help="Skip saving JSON files to data/ (Supabase only)",
    )
    args = parser.parse_args()
    asyncio.run(run(args.source, args.max, save_files=not args.no_files))


if __name__ == "__main__":
    main()
