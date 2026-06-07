#!/usr/bin/env python3
"""
Quick reader for local JSONL tweet files in data/tweets/.

Usage:
  python scripts/read_local.py                   # list all saved snapshots
  python scripts/read_local.py --source for_you  # show latest for_you file
  python scripts/read_local.py --source following --file 2026-06-07_1200.jsonl
  python scripts/read_local.py --search "palabra clave"
  python scripts/read_local.py --stats
"""

import argparse
import json
import sys
from pathlib import Path
from collections import Counter

TWEETS_DIR = Path(__file__).parent.parent / "data" / "tweets"


def list_files():
    files = sorted(TWEETS_DIR.rglob("*.jsonl"))
    if not files:
        print("No hay archivos JSONL en data/tweets/. Ejecuta primero:")
        print("  python scripts/fetch_tweets.py --local --no-supabase")
        return
    for f in files:
        lines = sum(1 for _ in f.open(encoding="utf-8"))
        rel = f.relative_to(TWEETS_DIR.parent.parent)
        print(f"  {rel}  ({lines} tweets)")


def load_jsonl(path: Path) -> list[dict]:
    tweets = []
    with path.open(encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                tweets.append(json.loads(line))
    return tweets


def latest_file(source: str) -> Path | None:
    d = TWEETS_DIR / source
    if not d.exists():
        return None
    files = sorted(d.glob("*.jsonl"))
    return files[-1] if files else None


def print_tweet(t: dict, index: int = None):
    author = t.get("author") or {}
    name = author.get("name", "?")
    handle = author.get("username", "?")
    text = t.get("text", "")
    created = t.get("created_at", "")
    metrics = t.get("metrics") or {}
    likes = metrics.get("likes", 0)
    rts = metrics.get("retweets", 0)
    prefix = f"[{index}] " if index is not None else ""
    print(f"{prefix}{name} @{handle}  {created}")
    print(f"  {text[:200]}")
    print(f"  ❤ {likes}  RT {rts}  {t.get('url','')}")
    print()


def show_tweets(source: str, filename: str | None, limit: int):
    if filename:
        path = TWEETS_DIR / source / filename
    else:
        path = latest_file(source)

    if not path or not path.exists():
        print(f"No se encontró archivo para source='{source}'")
        sys.exit(1)

    tweets = load_jsonl(path)
    print(f"--- {path.relative_to(TWEETS_DIR.parent.parent)} ({len(tweets)} tweets) ---\n")
    for i, t in enumerate(tweets[:limit]):
        print_tweet(t, i + 1)


def search_tweets(query: str):
    q = query.lower()
    found = 0
    for f in sorted(TWEETS_DIR.rglob("*.jsonl")):
        tweets = load_jsonl(f)
        matches = [t for t in tweets if q in (t.get("text") or "").lower()]
        if matches:
            rel = f.relative_to(TWEETS_DIR.parent.parent)
            print(f"\n=== {rel} ({len(matches)} coincidencias) ===\n")
            for t in matches:
                print_tweet(t)
            found += len(matches)
    if not found:
        print(f"No se encontraron tweets con '{query}'")
    else:
        print(f"Total: {found} tweets encontrados.")


def stats():
    all_tweets: list[dict] = []
    for f in TWEETS_DIR.rglob("*.jsonl"):
        all_tweets.extend(load_jsonl(f))

    if not all_tweets:
        print("No hay tweets guardados.")
        return

    # Deduplicate by id
    seen = {}
    for t in all_tweets:
        seen[t.get("id")] = t
    unique = list(seen.values())

    authors = Counter(
        (t.get("author") or {}).get("username", "?") for t in unique
    )
    hashtags = Counter()
    for t in unique:
        for h in (t.get("hashtags") or []):
            hashtags[h.lower()] += 1

    print(f"Tweets únicos guardados: {len(unique)}")
    print(f"\nTop 10 autores:")
    for handle, count in authors.most_common(10):
        print(f"  @{handle}: {count}")
    if hashtags:
        print(f"\nTop 10 hashtags:")
        for tag, count in hashtags.most_common(10):
            print(f"  #{tag}: {count}")


def main():
    parser = argparse.ArgumentParser(description="Lee y analiza tweets guardados localmente.")
    parser.add_argument("--source", choices=["for_you", "following", "mine"], default="for_you")
    parser.add_argument("--file", help="Nombre de archivo específico (ej: 2026-06-07_1200.jsonl)")
    parser.add_argument("--limit", type=int, default=20, help="Número de tweets a mostrar (default: 20)")
    parser.add_argument("--search", metavar="TEXTO", help="Buscar texto en todos los tweets guardados")
    parser.add_argument("--stats", action="store_true", help="Mostrar estadísticas de todos los tweets guardados")
    parser.add_argument("--list", action="store_true", help="Listar todos los archivos guardados")
    args = parser.parse_args()

    if args.list:
        list_files()
    elif args.stats:
        stats()
    elif args.search:
        search_tweets(args.search)
    else:
        show_tweets(args.source, args.file, args.limit)


if __name__ == "__main__":
    main()
