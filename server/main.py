"""
Flask web app for Tweets — serves the static frontend in web/ and exposes a
small JSON API. Reads from Supabase, refreshes via twikit on demand.
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
# Static routes
# ---------------------------------------------------------------------------

@app.route("/")
def index():
    return send_from_directory(str(WEB_DIR), "index.html")


@app.route("/healthz")
def healthz():
    return {"ok": True}


# ---------------------------------------------------------------------------
# API
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
        seen = set()
        target_ids = []
        for s in snaps:
            if s["source"] not in seen:
                seen.add(s["source"])
                target_ids.append(s["id"])
    elif selection == "all":
        target_ids = [s["id"] for s in snaps]
    else:
        abort(400, "selection must be all_latest, all, or snapshot")

    if not target_ids:
        return jsonify([])

    bridge = (
        client.table("snapshot_tweets")
        .select("tweet_id")
        .in_("snapshot_id", target_ids)
        .execute()
    )
    tweet_ids = list({b["tweet_id"] for b in (bridge.data or [])})
    if not tweet_ids:
        return jsonify([])

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
    unique.sort(key=lambda t: t.get("created_at") or "", reverse=True)

    return jsonify(unique)


@app.route("/api/tweets/<tweet_id>/replies")
def api_tweet_replies(tweet_id: str):
    """Returns replies to a tweet. Reads from Supabase first; on first call
    (no replies cached) or when ?refresh=1 is passed, fetches live via twikit
    and caches the result."""
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

    # Live fetch via twikit (sync wrapper around async).
    try:
        from scripts.fetch_tweets import (
            get_client as get_tw_client,
            fetch_replies,
            push_tweets_only,
        )
    except Exception as e:
        abort(500, f"fetch_tweets module unavailable: {e}")

    async def _do():
        tw = await get_tw_client()
        replies = await fetch_replies(tw, tweet_id, max_replies=80)
        push_tweets_only(client, replies)
        return replies

    try:
        replies = asyncio.run(_do())
    except Exception as e:
        abort(500, f"replies fetch failed: {e}")

    # Re-hydrate from DB so we get the canonical first_seen_at values.
    res = (
        client.table("tweets")
        .select("raw, first_seen_at, created_at")
        .in_("id", [r["id"] for r in replies] or ["__none__"])
        .execute()
    )
    hydrated = _hydrate(res.data or [])
    hydrated.sort(key=lambda t: t.get("created_at") or "")
    return jsonify({"replies": hydrated, "from_cache": False})


@app.route("/api/refresh", methods=["POST"])
def api_refresh():
    """Triggers an in-process fetch of the user's own tweets and pushes to Supabase."""
    source = request.args.get("source", "mine")
    if source not in ("mine", "timeline", "both"):
        abort(400, "source must be mine, timeline, or both")
    try:
        max_n = int(request.args.get("max", "50"))
    except ValueError:
        abort(400, "max must be an integer")

    try:
        from scripts.fetch_tweets import run as fetch_run
    except Exception as e:
        abort(500, f"fetch_tweets module unavailable: {e}")

    try:
        asyncio.run(fetch_run(source, max_n))
    except Exception as e:
        abort(500, f"refresh failed: {e}")

    snap = (
        sb()
        .table("snapshots")
        .select("id, fetched_at, source, count")
        .order("fetched_at", desc=True)
        .limit(1)
        .execute()
    )
    return jsonify({"ok": True, "latest_snapshot": (snap.data or [None])[0]})


# ---------------------------------------------------------------------------
# Local dev entrypoint
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", "8080")), debug=True)
