"""
Flask web app for Tweets — serves the static frontend in web/ and exposes a
JSON API. Reads from Supabase, refreshes via twikit on demand, and proxies
write actions (like / retweet / bookmark / reply) back to X using the same
session cookies that the cron service uses to fetch.
"""

import asyncio
import html as _html
import os
import re
import time
import urllib.parse
import urllib.request
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
        .select("tweet_id, snapshot_id")
        .in_("snapshot_id", target_ids)
        .execute()
    )
    bridge_rows = bridge.data or []
    tweet_ids = list({b["tweet_id"] for b in bridge_rows})
    if not tweet_ids:
        return jsonify({"tweets": [], "total": 0, "has_more": False})

    # snapshot_id -> source map for attribution
    snap_to_source = {s["id"]: s["source"] for s in snaps}
    tweet_sources: dict[str, set[str]] = {}
    for b in bridge_rows:
        src = snap_to_source.get(b["snapshot_id"])
        if not src:
            continue
        tweet_sources.setdefault(b["tweet_id"], set()).add(src)

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
        srcs = tweet_sources.get(tid)
        if srcs:
            t["_sources"] = sorted(srcs)
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
# Link preview (OG / Twitter cards)
# ---------------------------------------------------------------------------

# In-memory TTL cache. Keyed by URL. Stores (expiry_ts, payload).
_OG_CACHE: dict[str, tuple[float, dict]] = {}
_OG_TTL_SECONDS = 60 * 60 * 12  # 12h
_OG_MAX_BYTES = 200_000          # only read first 200kb of HTML
_OG_TIMEOUT = 6                  # seconds

_META_RE = re.compile(
    r'<meta\s+[^>]*?(?:property|name)\s*=\s*["\']([^"\']+)["\'][^>]*?content\s*=\s*["\']([^"\']*)["\'][^>]*?/?>',
    re.IGNORECASE,
)
_META_RE_REV = re.compile(
    r'<meta\s+[^>]*?content\s*=\s*["\']([^"\']*)["\'][^>]*?(?:property|name)\s*=\s*["\']([^"\']+)["\'][^>]*?/?>',
    re.IGNORECASE,
)
_TITLE_RE = re.compile(r'<title[^>]*>([^<]+)</title>', re.IGNORECASE | re.DOTALL)


def _abs_url(base: str, href: str) -> str:
    if not href:
        return href
    return urllib.parse.urljoin(base, href)


def _fetch_html(url: str) -> tuple[str, str]:
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": "Mozilla/5.0 (compatible; TweetsBot/1.0; +https://tweets.app)",
            "Accept": "text/html,application/xhtml+xml",
            "Accept-Language": "es,en;q=0.8",
        },
    )
    with urllib.request.urlopen(req, timeout=_OG_TIMEOUT) as resp:
        final_url = resp.geturl()
        ctype = resp.headers.get("Content-Type", "") or ""
        if "html" not in ctype.lower():
            return final_url, ""
        # Charset
        charset = "utf-8"
        m = re.search(r"charset=([\w\-]+)", ctype, re.IGNORECASE)
        if m:
            charset = m.group(1)
        body = resp.read(_OG_MAX_BYTES)
    try:
        return final_url, body.decode(charset, errors="replace")
    except LookupError:
        return final_url, body.decode("utf-8", errors="replace")


def _parse_meta(htmlsrc: str) -> dict:
    metas: dict[str, str] = {}
    for key, val in _META_RE.findall(htmlsrc):
        metas.setdefault(key.lower(), _html.unescape(val))
    for val, key in _META_RE_REV.findall(htmlsrc):
        metas.setdefault(key.lower(), _html.unescape(val))
    return metas


def _build_preview(url: str) -> dict:
    final_url, src = _fetch_html(url)
    if not src:
        return {"url": final_url or url, "kind": "link"}
    metas = _parse_meta(src)
    title = (
        metas.get("og:title")
        or metas.get("twitter:title")
        or (_TITLE_RE.search(src).group(1).strip() if _TITLE_RE.search(src) else "")
    )
    description = (
        metas.get("og:description")
        or metas.get("twitter:description")
        or metas.get("description")
        or ""
    )
    image = (
        metas.get("og:image")
        or metas.get("twitter:image")
        or metas.get("twitter:image:src")
        or ""
    )
    site = metas.get("og:site_name") or ""
    parsed = urllib.parse.urlparse(final_url or url)
    domain = parsed.netloc.replace("www.", "")
    return {
        "url": final_url or url,
        "title": (title or "").strip()[:280],
        "description": (description or "").strip()[:400],
        "image": _abs_url(final_url or url, image) if image else "",
        "site": site or domain,
        "domain": domain,
        "kind": "link",
    }


def _enrich_kind(payload: dict) -> dict:
    """Tag known-domains so the frontend can render a richer card."""
    domain = (payload.get("domain") or "").lower()
    parsed = urllib.parse.urlparse(payload.get("url", ""))
    path = parsed.path or ""
    if domain.endswith("github.com") or domain == "github.com":
        m = re.match(r"^/([^/]+)/([^/]+)/?$", path)
        if m:
            payload["kind"] = "github"
            payload["github_owner"] = m.group(1)
            payload["github_repo"] = m.group(2)
    elif domain.endswith("youtube.com") or domain == "youtu.be":
        if domain == "youtu.be":
            vid = path.strip("/").split("/")[0] or ""
        else:
            qs = urllib.parse.parse_qs(parsed.query or "")
            vid = (qs.get("v") or [""])[0]
            if not vid:
                m = re.match(r"^/(?:shorts|embed)/([^/]+)", path)
                if m:
                    vid = m.group(1)
        if vid:
            payload["kind"] = "youtube"
            payload["youtube_id"] = vid
    return payload


@app.route("/api/og")
def api_og():
    url = (request.args.get("url") or "").strip()
    if not url or not (url.startswith("http://") or url.startswith("https://")):
        abort(400, "valid http(s) url required")

    # Block private/internal targets.
    parsed = urllib.parse.urlparse(url)
    host = (parsed.hostname or "").lower()
    if not host or host in {"localhost"} or host.endswith(".local"):
        abort(400, "host not allowed")
    if host.startswith("127.") or host.startswith("10.") or host.startswith("192.168."):
        abort(400, "host not allowed")

    now = time.time()
    cached = _OG_CACHE.get(url)
    if cached and cached[0] > now:
        return jsonify(cached[1])

    try:
        payload = _build_preview(url)
    except Exception as e:
        payload = {"url": url, "kind": "link", "error": str(e)[:200]}
    parsed_for_kind = urllib.parse.urlparse(payload.get("url") or url)
    payload.setdefault("domain", (parsed_for_kind.netloc or "").replace("www.", ""))
    payload = _enrich_kind(payload)

    _OG_CACHE[url] = (now + _OG_TTL_SECONDS, payload)
    # Cap cache size
    if len(_OG_CACHE) > 2000:
        for k in list(_OG_CACHE.keys())[:500]:
            _OG_CACHE.pop(k, None)

    resp = jsonify(payload)
    resp.headers["Cache-Control"] = "public, max-age=3600"
    return resp


# ---------------------------------------------------------------------------
# Translate (free MyMemory backend, with an in-memory cache)
# ---------------------------------------------------------------------------

_TR_CACHE: dict[tuple[str, str, str], tuple[float, dict]] = {}
_TR_TTL_SECONDS = 60 * 60 * 24 * 7  # 7 days
_TR_MAX_CHARS = 800                 # tweet text is ~280, plus we trim safely


@app.route("/api/translate", methods=["POST"])
def api_translate():
    body = request.get_json(silent=True) or {}
    text = (body.get("text") or "").strip()
    source = (body.get("source") or "auto").lower().strip()[:8] or "auto"
    target = (body.get("target") or "es").lower().strip()[:8] or "es"
    if not text:
        abort(400, "text required")
    if source == target and source != "auto":
        return jsonify({"text": text, "source": source, "target": target, "cached": False})
    if len(text) > _TR_MAX_CHARS:
        text = text[:_TR_MAX_CHARS]

    key = (text, source, target)
    now = time.time()
    cached = _TR_CACHE.get(key)
    if cached and cached[0] > now:
        return jsonify({**cached[1], "cached": True})

    # MyMemory: anonymous endpoint, no key required, ~5k chars/day per IP.
    # Their langpair format is "src|tgt", with "autodetect" for source.
    src_param = "autodetect" if source == "auto" else source
    qs = urllib.parse.urlencode({
        "q": text,
        "langpair": f"{src_param}|{target}",
        "de": "tweets@app.local",  # courtesy contact for higher anon quota
    })
    url = f"https://api.mymemory.translated.net/get?{qs}"
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "TweetsBot/1.0"})
        with urllib.request.urlopen(req, timeout=8) as resp:
            raw = resp.read(64_000)
        import json as _json
        data = _json.loads(raw.decode("utf-8", errors="replace"))
        translated = (data.get("responseData") or {}).get("translatedText") or ""
        detected_src = (data.get("responseData") or {}).get("detectedLanguage") or source
        if not translated:
            abort(502, "translation backend returned empty result")
        payload = {
            "text": translated,
            "source": detected_src or source,
            "target": target,
            "provider": "mymemory",
        }
    except Exception as e:
        abort(502, f"translation failed: {e}")

    _TR_CACHE[key] = (now + _TR_TTL_SECONDS, payload)
    if len(_TR_CACHE) > 5000:
        for k in list(_TR_CACHE.keys())[:1000]:
            _TR_CACHE.pop(k, None)
    return jsonify({**payload, "cached": False})


# ---------------------------------------------------------------------------
# Local dev entrypoint
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", "8080")), debug=True)
