"""
Flask web app for Tweets — serves the static frontend in web/ and exposes a
small JSON API that proxies queries to Supabase. The Supabase URL and key
stay server-side so the browser never sees them directly.
"""

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
        .select("id, fetched_at, source, username, count, file")
        .order("fetched_at", desc=True)
        .execute()
    )
    return jsonify(res.data or [])


@app.route("/api/tweets")
def api_tweets():
    """
    Returns tweets in the same shape as the JSON snapshots so the existing
    frontend logic keeps working.

    Query params:
      selection: 'all_latest' (default) | 'all' | 'snapshot'
      id: snapshot id (when selection='snapshot')
      source: 'all' (default) | 'timeline' | 'mine'
    """
    selection = request.args.get("selection", "all_latest")
    source = request.args.get("source", "all")
    snapshot_id_param = request.args.get("id")

    client = sb()

    # Resolve the set of snapshot ids we care about.
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

    # Tweet ids that appear in those snapshots.
    bridge = (
        client.table("snapshot_tweets")
        .select("tweet_id")
        .in_("snapshot_id", target_ids)
        .execute()
    )
    tweet_ids = list({b["tweet_id"] for b in (bridge.data or [])})
    if not tweet_ids:
        return jsonify([])

    # Pull the raw column — already shaped exactly like a snapshot tweet.
    tweets: list[dict] = []
    CHUNK = 800
    for i in range(0, len(tweet_ids), CHUNK):
        chunk = tweet_ids[i:i + CHUNK]
        res = (
            client.table("tweets")
            .select("raw, created_at")
            .in_("id", chunk)
            .execute()
        )
        for row in (res.data or []):
            if row.get("raw"):
                tweets.append(row["raw"])

    # Dedup + newest first.
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


# ---------------------------------------------------------------------------
# Local dev entrypoint
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", "8080")), debug=True)
