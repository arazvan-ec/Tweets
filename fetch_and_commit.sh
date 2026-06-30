#!/usr/bin/env bash
# Fetch tweets from your timeline and commit the JSONL files to the repo.
# Run this locally (or from any machine with git credentials) on a schedule:
#   crontab -e
#   0 * * * * cd /path/to/Tweets && bash fetch_and_commit.sh >> fetch.log 2>&1
set -euo pipefail

SOURCE="${1:-all}"   # for_you | following | mine | all_feeds | all
MAX="${2:-100}"

echo "=== $(date -u '+%Y-%m-%d %H:%M:%S UTC') ==="

python scripts/fetch_tweets.py --source "$SOURCE" --max "$MAX" --save-local

# Only commit if there are changes
if git diff --quiet data/; then
  echo "No new tweets — nothing to commit."
else
  git add data/tweets/
  git commit -m "tweets: $(date -u '+%Y-%m-%d %H:%M UTC') [${SOURCE}]"
  git push
  echo "Committed and pushed."
fi
