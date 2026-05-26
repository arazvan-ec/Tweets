#!/usr/bin/env python3
"""
Descarga tweets del timeline y los guarda como archivos JSONL en data/tweets/.

Uso:
    python scripts/save_to_repo.py                  # For You + Following
    python scripts/save_to_repo.py --source for_you
    python scripts/save_to_repo.py --source following
    python scripts/save_to_repo.py --source mine
    python scripts/save_to_repo.py --source all     # For You + Following + propios
    python scripts/save_to_repo.py --max 200

Formato de salida:
    data/tweets/YYYY-MM-DD_HHMM_<source>.jsonl   — un tweet por línea (JSON)
    data/tweets/index.json                         — índice de todos los archivos
"""

import argparse
import asyncio
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

REPO_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = REPO_ROOT / "data" / "tweets"
INDEX_FILE = DATA_DIR / "index.json"


# ---------------------------------------------------------------------------
# Persistence helpers
# ---------------------------------------------------------------------------

def _load_index() -> list[dict]:
    if INDEX_FILE.exists():
        try:
            return json.loads(INDEX_FILE.read_text())
        except Exception:
            return []
    return []


def _save_index(entries: list[dict]):
    INDEX_FILE.write_text(json.dumps(entries, ensure_ascii=False, indent=2))


def _load_seen_ids() -> set[str]:
    """Returns all tweet IDs already saved across every JSONL file."""
    seen: set[str] = set()
    for f in DATA_DIR.glob("*.jsonl"):
        for line in f.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
                tid = obj.get("id")
                if tid:
                    seen.add(str(tid))
            except Exception:
                pass
    return seen


def save_batch(tweets: list[dict], source: str) -> Path | None:
    """
    Saves a list of tweet dicts to a new JSONL file.
    Returns the file path, or None if there was nothing new to save.
    """
    DATA_DIR.mkdir(parents=True, exist_ok=True)

    seen_ids = _load_seen_ids()
    new_tweets = [t for t in tweets if str(t.get("id", "")) not in seen_ids]

    if not new_tweets:
        print(f"  [{source}] No hay tweets nuevos (todos ya estaban guardados).")
        return None

    now = datetime.now(timezone.utc)
    filename = f"{now.strftime('%Y-%m-%d_%H%M')}_{source}.jsonl"
    out_path = DATA_DIR / filename

    with out_path.open("w", encoding="utf-8") as f:
        for t in new_tweets:
            f.write(json.dumps(t, ensure_ascii=False) + "\n")

    print(f"  [{source}] {len(new_tweets)} tweets nuevos → {out_path.relative_to(REPO_ROOT)}")

    # Update index
    entries = _load_index()
    entries.append({
        "file": str(out_path.relative_to(REPO_ROOT)),
        "source": source,
        "fetched_at": now.isoformat(),
        "count": len(new_tweets),
    })
    _save_index(entries)

    return out_path


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

async def run(source: str, max_tweets: int):
    from scripts.fetch_tweets import (
        get_client,
        fetch_for_you,
        fetch_following,
        fetch_own_tweets,
    )

    username = os.getenv("TWITTER_USERNAME")
    client = await get_client()

    sources_map = {
        "for_you":    [("for_you",)],
        "following":  [("following",)],
        "mine":       [("mine",)],
        "timeline":   [("for_you",)],
        "all_feeds":  [("for_you",), ("following",)],
        "all":        [("for_you",), ("following",), ("mine",)],
    }
    plan = sources_map.get(source)
    if plan is None:
        print(f"ERROR: fuente desconocida '{source}'")
        sys.exit(1)

    for (kind,) in plan:
        if kind == "for_you":
            tweets = await fetch_for_you(client, max_tweets)
        elif kind == "following":
            tweets = await fetch_following(client, max_tweets)
        elif kind == "mine":
            if not username:
                print("ERROR: TWITTER_USERNAME no está configurado en .env")
                sys.exit(1)
            tweets = await fetch_own_tweets(client, username, max_tweets)
        else:
            continue
        save_batch(tweets, kind)

    print("\nListo.")


def main():
    parser = argparse.ArgumentParser(
        description="Descarga tweets y los guarda en data/tweets/ dentro del repo."
    )
    parser.add_argument(
        "--source",
        choices=["for_you", "following", "mine", "timeline", "all_feeds", "all"],
        default="all_feeds",
        help="Qué feed descargar (por defecto: all_feeds = for_you + following)",
    )
    parser.add_argument(
        "--max",
        type=int,
        default=100,
        help="Máximo de tweets por fuente (por defecto: 100)",
    )
    args = parser.parse_args()
    asyncio.run(run(args.source, args.max))


if __name__ == "__main__":
    main()
