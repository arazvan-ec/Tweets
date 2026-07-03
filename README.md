# Tweets

Scrapes your X/Twitter account's Home feed (For You + Following) and your
own tweets using [twikit](https://github.com/d60/twikit) — no official/paid
X API required, it just reads what your logged-in account would see, the
same way the twitter.com website does.

## Components

- `scripts/fetch_tweets.py` — fetches tweets and pushes them to Supabase.
  Runs continuously via Railway cron (`railway.cron.toml`), backing the
  Flask web app (`server/`, `web/`) that lets you browse/search/like/reply.
- `scripts/archive_to_repo.py` — fetches the same feeds and writes them as
  JSON files straight into this repo under `data/archive/`, so tweet
  content is versioned in git itself for offline reading/analysis/
  comparison, independent of Supabase. Runs on a schedule via
  `.github/workflows/archive-tweets.yml` (every 6 hours by default).

## Archive format (`data/archive/`)

```
data/archive/
  for_you/
    2026-07-03T120000Z.json   # one immutable snapshot per run
    index.json                # list of all snapshots for this source
  following/
    ...
  mine/
    ...
```

Each snapshot file looks like:

```json
{
  "source": "for_you",
  "username": "yourhandle",
  "fetched_at": "2026-07-03T12:00:00+00:00",
  "count": 100,
  "tweets": [ { "id": "...", "text": "...", "author": {...}, "metrics": {...}, ... } ]
}
```

Nothing is overwritten — every run adds a new file — so you can diff or
aggregate snapshots later to see how the feed changed over time.

## Setup for the repo-archiving routine

Add these as **repository secrets** (Settings → Secrets and variables →
Actions):

- `TWITTER_COOKIES_JSON` — contents of `data/.cookies.json` after logging in
  once locally (`python scripts/fetch_tweets.py`). Strongly preferred over
  email/password, since logging in fresh from GitHub's shared runner IPs is
  likely to get flagged by X's anti-bot checks.
- `TWITTER_USERNAME` — your handle, without the `@` (needed to fetch "mine").
- `TWITTER_EMAIL` / `TWITTER_PASSWORD` — fallback only, used if no cookies
  secret is set.

The workflow only starts firing on its schedule once it exists on the
default branch. You can also trigger it manually from the Actions tab
(`workflow_dispatch`).
