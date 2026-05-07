"""
Flask web app for Tweets — serves the static frontend in web/ and exposes a
JSON API. Reads from Supabase, refreshes via twikit on demand, and proxies
write actions (like / retweet / bookmark / reply) back to X using the same
session cookies that the cron service uses to fetch.
"""

import asyncio
import os
from pathlib import Path

from flask import Flask, jsonify, request, send_from_directory, abort
from supabase import Client, create_client
from dotenv import load_dotenv

load_dotenv()

ROOT = Path(__file__).resolve().parent.parent
WEB_DIR = ROOT / "web"

app = Flask(__name__, static_folder=str(WEB_DIR), static_url_path="")


# ---------------------------------------------------------------------------
# Supabase client (lazy)
# ---------------------------------------------------------------------------

_sb: Client | None = None


def sb() -> Client:
    global _sb
    if _sb is None:
        url = os.getenv("SUPABASE_URL")
        key = os.getenv("SUPABASE_KEY")
        if not url or not key:
            abort(500, "SUPABASE_URL / SUPABASE_KEY not configured")
        _sb = create_client(url, key)
    return _sb


# ---------------------------------------------------------------------------
# twikit client (lazy, async). The same cookies the cron uses.
# ---------------------------------------------------------------------------

_tw = None


async def tw_client():
    global _tw
    if _tw is None:
        from scripts.fetch_tweets import get_client as get_tw_client
        _tw = await get_tw_client()
    return _tw


def run_async(coro):
    """Sync wrapper for our async helpers — Flask handlers are sync."""
    try:
        return asyncio.run(coro)
    except RuntimeError:
        # Already inside a loop (rare under gunicorn sync workers, but guard)
        loop = asyncio.new_event_loop()
        try:
            return loop.run_until_complete(coro)
        finally:
            loop.close()


# ---------------------------------------------------------------------------
# Static routes
# ---------------------------------------------------------------------------

@app.route("/")
def index():
    return send_from_directory(str(WEB_DIR), "index.html")


# Serve /u/<handle> by returning the same SPA — the frontend's hash router
# handles the actual profile rendering.
@app.route("/u/<handle>")
def index_profile(handle):
    return send_from_directory(str(WEB_DIR), "index.html")


@app.route("/healthz")
def healthz():
    return {"ok": True}


# ---------------------------------------------------------------------------
# Read API
# ---------------------------------------------------------------------------

@app.route("/api/snapshots")
def api_snapshots():
    res = (
        sb()
        .table("snapshots")
        .select("id, fetched_at, source, username, count")
        .order("fetched_at", desc=True)
        .execute()
    )
    return jsonify(res.data or [])


def _hydrate(rows: list[dict]) -> list[dict]:
    """Merge DB metadata (first_seen_at) into the raw tweet dicts."""
    out: list[dict] = []
    for r in rows:
        raw = r.get("raw")
        if not raw:
            continue
        raw["_first_seen_at"] = r.get("first_seen_at")
        out.append(raw)
    return out


@app.route("/api/tweets")
def api_tweets():
    selection = request.args.get("selection", "all_latest")
    source = request.args.get("source", "all")
    snapshot_id_param = request.args.get("id")
    try:
        limit = int(request.args.get("limit", "50"))
    except ValueError:
        limit = 50
    try:
        offset = int(request.args.get("offset", "0"))
    except ValueError:
        offset = 0
    try:
        # How many recent snapshots PER SOURCE to merge when selection=all_latest.
        # Default 1 = exactly the latest snapshot per source (legacy behaviour).
        window = max(1, int(request.args.get("window", "1")))
    except ValueError:
        window = 1

    client = sb()

    snaps_q = (
        client.table("snapshots")
        .select("id, source, fetched_at")
        .order("fetched_at", desc=True)
    )
    if source != "all":
        snaps_q = snaps_q.eq("source", source)
    snaps = snaps_q.execute().data or []

    if selection == "snapshot":
        try:
            target_ids = [int(snapshot_id_param)] if snapshot_id_param else []
        except ValueError:
            abort(400, "id must be an integer")
    elif selection == "all_latest":
        per_src: dict[str, int] = {}
        target_ids = []
        for s in snaps:
            n = per_src.get(s["source"], 0)
            if n < window:
                per_src[s["source"]] = n + 1
                target_ids.append(s["id"])
    elif selection == "all":
        target_ids = [s["id"] for s in snaps]
    else:
        abort(400, "selection must be all_latest, all, or snapshot")

    if not target_ids:
        return jsonify({"tweets": [], "total": 0, "has_more": False})

    bridge = (
        client.table("snapshot_tweets")
        .select("tweet_id")
        .in_("snapshot_id", target_ids)
        .execute()
    )
    tweet_ids = list({b["tweet_id"] for b in (bridge.data or [])})
    if not tweet_ids:
        return jsonify({"tweets": [], "total": 0, "has_more": False})

    rows: list[dict] = []
    CHUNK = 800
    for i in range(0, len(tweet_ids), CHUNK):
        chunk = tweet_ids[i:i + CHUNK]
        res = (
            client.table("tweets")
            .select("raw, first_seen_at, created_at")
            .in_("id", chunk)
            .execute()
        )
        rows.extend(res.data or [])

    tweets = _hydrate(rows)

    seen = set()
    unique = []
    for t in tweets:
        tid = t.get("id")
        if not tid or tid in seen:
            continue
        seen.add(tid)
        unique.append(t)
    # Sort by capture time (newest captures first) so freshly-seen tweets
    # bubble up. Ties broken by the tweet's own creation date so two tweets
    # captured in the same snapshot keep a stable order.
    unique.sort(
        key=lambda t: (t.get("_first_seen_at") or "", t.get("created_at") or ""),
        reverse=True,
    )

    total = len(unique)
    page = unique[offset:offset + limit]
    return jsonify({
        "tweets": page,
        "total": total,
        "has_more": offset + limit < total,
        "offset": offset,
        "limit": limit,
    })


@app.route("/api/tweets/<tweet_id>/replies")
def api_tweet_replies(tweet_id: str):
    refresh = request.args.get("refresh") == "1"
    client = sb()

    if not refresh:
        cached = (
            client.table("tweets")
            .select("raw, first_seen_at, created_at")
            .eq("in_reply_to_id", tweet_id)
            .execute()
        )
        rows = cached.data or []
        if rows:
            replies = _hydrate(rows)
            replies.sort(key=lambda t: t.get("created_at") or "")
            return jsonify({"replies": replies, "from_cache": True})

    try:
        from scripts.fetch_tweets import fetch_replies, push_tweets_only
    except Exception as e:
        abort(500, f"fetch_tweets module unavailable: {e}")

    async def _do():
        tw = await tw_client()
        replies = await fetch_replies(tw, tweet_id, max_replies=80)
        push_tweets_only(client, replies)
        return replies

    try:
        replies = run_async(_do())
    except Exception as e:
        abort(500, f"replies fetch failed: {e}")

    res = (
        client.table("tweets")
        .select("raw, first_seen_at, created_at")
        .in_("id", [r["id"] for r in replies] or ["__none__"])
        .execute()
    )
    hydrated = _hydrate(res.data or [])
    hydrated.sort(key=lambda t: t.get("created_at") or "")
    return jsonify({"replies": hydrated, "from_cache": False})


# ---------------------------------------------------------------------------
# Profile API
# ---------------------------------------------------------------------------

def _user_to_dict(u) -> dict:
    return {
        "id": str(getattr(u, "id", "") or ""),
        "name": getattr(u, "name", None),
        "username": getattr(u, "screen_name", None),
        "avatar": getattr(u, "profile_image_url", None),
        "banner": getattr(u, "profile_banner_url", None),
        "url": getattr(u, "url", None),
        "location": getattr(u, "location", None),
        "description": getattr(u, "description", None),
        "verified": bool(getattr(u, "verified", False)),
        "is_blue_verified": bool(getattr(u, "is_blue_verified", False)),
        "followers_count": getattr(u, "followers_count", 0) or 0,
        "following_count": getattr(u, "following_count", 0) or 0,
        "statuses_count": getattr(u, "statuses_count", 0) or 0,
        "created_at": getattr(u, "created_at", None),
    }


@app.route("/api/users/<handle>")
def api_user(handle: str):
    """Looks up a Twitter user by screen_name. Live fetch via twikit."""
    async def _do():
        tw = await tw_client()
        return await tw.get_user_by_screen_name(handle)
    try:
        u = run_async(_do())
    except Exception as e:
        abort(404, f"user lookup failed: {e}")
    return jsonify(_user_to_dict(u))


@app.route("/api/users/<handle>/tweets")
def api_user_tweets(handle: str):
    """Fetches a user's recent tweets (live) and caches them."""
    try:
        max_n = int(request.args.get("max", "40"))
    except ValueError:
        max_n = 40
    tweet_type = request.args.get("type", "Tweets")
    if tweet_type not in ("Tweets", "Replies", "Media", "Likes"):
        abort(400, "type must be Tweets, Replies, Media or Likes")

    try:
        from scripts.fetch_tweets import tweet_to_dict, push_tweets_only
    except Exception as e:
        abort(500, f"fetch_tweets module unavailable: {e}")

    async def _do():
        tw = await tw_client()
        u = await tw.get_user_by_screen_name(handle)
        results = await u.get_tweets(tweet_type, count=20)
        out = []
        while results and len(out) < max_n:
            for t in results:
                out.append(tweet_to_dict(t))
            if len(out) >= max_n:
                break
            try:
                results = await results.next()
            except Exception:
                break
        return out[:max_n]

    try:
        tweets = run_async(_do())
    except Exception as e:
        abort(500, f"user tweets fetch failed: {e}")

    push_tweets_only(sb(), tweets)
    return jsonify({"user": handle, "type": tweet_type, "tweets": tweets})


# ---------------------------------------------------------------------------
# Refresh
# ---------------------------------------------------------------------------

@app.route("/api/refresh", methods=["POST"])
def api_refresh():
    """Triggers an in-process fetch of one or more feeds and pushes to Supabase."""
    source = request.args.get("source", "all_feeds")
    valid = {"for_you", "following", "mine", "timeline", "both", "all_feeds", "all"}
    if source not in valid:
        abort(400, f"source must be one of {sorted(valid)}")
    try:
        max_n = int(request.args.get("max", "50"))
    except ValueError:
        abort(400, "max must be an integer")

    try:
        from scripts.fetch_tweets import run as fetch_run
    except Exception as e:
        abort(500, f"fetch_tweets module unavailable: {e}")

    try:
        run_async(fetch_run(source, max_n))
    except Exception as e:
        abort(500, f"refresh failed: {e}")

    snap = (
        sb()
        .table("snapshots")
        .select("id, fetched_at, source, count")
        .order("fetched_at", desc=True)
        .limit(3)
        .execute()
    )
    return jsonify({"ok": True, "latest_snapshots": snap.data or []})


# ---------------------------------------------------------------------------
# Write actions
# ---------------------------------------------------------------------------

def _wrap_write(fn):
    """Run an async twikit action and return {ok: True} or 500."""
    try:
        run_async(fn())
        return jsonify({"ok": True})
    except Exception as e:
        abort(500, f"action failed: {e}")


@app.route("/api/tweets/<tweet_id>/like", methods=["POST"])
def api_like(tweet_id: str):
    async def _do():
        tw = await tw_client()
        await tw.favorite_tweet(tweet_id)
    return _wrap_write(_do)


@app.route("/api/tweets/<tweet_id>/like", methods=["DELETE"])
def api_unlike(tweet_id: str):
    async def _do():
        tw = await tw_client()
        await tw.unfavorite_tweet(tweet_id)
    return _wrap_write(_do)


@app.route("/api/tweets/<tweet_id>/retweet", methods=["POST"])
def api_retweet(tweet_id: str):
    async def _do():
        tw = await tw_client()
        await tw.retweet(tweet_id)
    return _wrap_write(_do)


@app.route("/api/tweets/<tweet_id>/retweet", methods=["DELETE"])
def api_unretweet(tweet_id: str):
    async def _do():
        tw = await tw_client()
        await tw.delete_retweet(tweet_id)
    return _wrap_write(_do)


@app.route("/api/tweets/<tweet_id>/bookmark", methods=["POST"])
def api_bookmark(tweet_id: str):
    async def _do():
        tw = await tw_client()
        await tw.bookmark_tweet(tweet_id)
    return _wrap_write(_do)


@app.route("/api/tweets/<tweet_id>/bookmark", methods=["DELETE"])
def api_unbookmark(tweet_id: str):
    async def _do():
        tw = await tw_client()
        await tw.delete_bookmark(tweet_id)
    return _wrap_write(_do)


@app.route("/api/tweets/<tweet_id>/reply", methods=["POST"])
def api_reply(tweet_id: str):
    body = request.get_json(silent=True) or {}
    text = (body.get("text") or "").strip()
    if not text:
        abort(400, "text required")
    if len(text) > 280:
        abort(400, "text exceeds 280 characters")

    try:
        from scripts.fetch_tweets import tweet_to_dict, push_tweets_only
    except Exception as e:
        abort(500, f"fetch_tweets module unavailable: {e}")

    async def _do():
        tw = await tw_client()
        new_tweet = await tw.create_tweet(text=text, reply_to=tweet_id)
        return new_tweet

    try:
        new_tweet = run_async(_do())
    except Exception as e:
        abort(500, f"reply failed: {e}")

    serialized = tweet_to_dict(new_tweet)
    try:
        push_tweets_only(sb(), [serialized])
    except Exception:
        pass

    return jsonify({"ok": True, "tweet": serialized})


# ---------------------------------------------------------------------------
# Local dev entrypoint
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", "8080")), debug=True)
