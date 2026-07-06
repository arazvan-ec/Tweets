# Tweet archive

JSONL archive of tweets fetched via `scripts/fetch_tweets.py --save-local`,
one file per `(source, username)`:

```
data/tweets/<source>/<username>.jsonl
```

- `mine/<username>.jsonl` — tweets posted by your own account (kept up to
  date by the "Archive my tweets" GitHub Actions workflow, every 3h).
- `for_you/<username>.jsonl`, `following/<username>.jsonl` — your home
  timeline feeds, if you run the fetcher with those sources too.

Each line is one tweet object (same shape as `tweet_to_dict()` in
`scripts/fetch_tweets.py`: id, text, created_at, author, metrics, media,
urls, hashtags, quoted/retweeted tweet, etc.), plus:

- `first_seen_at` — when this tweet was first archived (UTC ISO 8601).
- `last_seen_at` — when it was last refreshed (metrics get updated on
  every run; the tweet itself is never deleted from the archive).

Files are rewritten on every run, sorted newest-first by tweet id. Load with
e.g. `pandas.read_json(path, lines=True)` or plain `json.loads` per line.
