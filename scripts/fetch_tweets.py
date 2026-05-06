#!/usr/bin/env python3
"""
Fetches tweets from X/Twitter home timeline using browser automation.
Saves results as JSON in data/YYYY-MM-DD/.
"""

import asyncio
import json
import os
import re
import sys
import argparse
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv
from playwright.async_api import async_playwright, Page, BrowserContext

load_dotenv()

DATA_DIR = Path(__file__).parent.parent / "data"
COOKIES_FILE = DATA_DIR / ".session_cookies.json"


# ---------------------------------------------------------------------------
# Session helpers
# ---------------------------------------------------------------------------

async def save_cookies(context: BrowserContext):
    cookies = await context.cookies()
    COOKIES_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(COOKIES_FILE, "w") as f:
        json.dump(cookies, f)


async def load_cookies(context: BrowserContext) -> bool:
    if not COOKIES_FILE.exists():
        return False
    with open(COOKIES_FILE) as f:
        cookies = json.load(f)
    await context.add_cookies(cookies)
    return True


# ---------------------------------------------------------------------------
# Login
# ---------------------------------------------------------------------------

async def login(page: Page, email: str, password: str, username: str):
    print("Logging in to X/Twitter...")
    await page.goto("https://x.com/i/flow/login", wait_until="domcontentloaded")
    await page.wait_for_timeout(2000)

    # Step 1: email / username
    email_input = page.locator('input[autocomplete="username"]')
    await email_input.wait_for(timeout=15000)
    await email_input.fill(email)
    await page.keyboard.press("Enter")
    await page.wait_for_timeout(2000)

    # Sometimes X asks to confirm username (unusual activity check)
    unusual = page.locator('input[data-testid="ocfEnterTextTextInput"]')
    try:
        await unusual.wait_for(timeout=4000)
        print("  Unusual activity check — entering username...")
        await unusual.fill(username)
        await page.keyboard.press("Enter")
        await page.wait_for_timeout(2000)
    except Exception:
        pass

    # Step 2: password
    pwd_input = page.locator('input[type="password"]')
    await pwd_input.wait_for(timeout=15000)
    await pwd_input.fill(password)
    await page.keyboard.press("Enter")
    await page.wait_for_timeout(3000)

    # Check for 2FA / phone verification
    current = page.url
    if "challenge" in current or "verify" in current or "confirm" in current:
        print("\nERROR: X is asking for 2FA or phone verification.")
        print("Please disable 2FA temporarily or verify the account manually, then retry.")
        sys.exit(1)

    # Wait for home
    try:
        await page.wait_for_url("**/home", timeout=20000)
    except Exception:
        if "/home" not in page.url:
            print(f"\nERROR: Login may have failed. Current URL: {page.url}")
            sys.exit(1)

    print(f"  Logged in as @{username}")


async def ensure_logged_in(page: Page, context: BrowserContext, email: str, password: str, username: str):
    loaded = await load_cookies(context)
    if loaded:
        await page.goto("https://x.com/home", wait_until="domcontentloaded")
        await page.wait_for_timeout(3000)
        if "/home" in page.url:
            print(f"  Session restored for @{username}")
            return
        print("  Saved session expired, logging in again...")

    await login(page, email, password, username)
    await save_cookies(context)


# ---------------------------------------------------------------------------
# Tweet extraction
# ---------------------------------------------------------------------------

async def extract_tweet(article) -> dict | None:
    try:
        # Tweet URL and ID
        link = article.locator('a[href*="/status/"]').first
        href = await link.get_attribute("href", timeout=2000)
        match = re.search(r"/status/(\d+)", href or "")
        if not match:
            return None
        tweet_id = match.group(1)

        # Text (can be empty for image-only tweets)
        text = ""
        text_el = article.locator('[data-testid="tweetText"]').first
        try:
            text = await text_el.inner_text(timeout=2000)
        except Exception:
            pass

        # Author name + handle
        author_name = ""
        author_handle = ""
        user_block = article.locator('[data-testid="User-Name"]').first
        try:
            raw = await user_block.inner_text(timeout=2000)
            parts = [p.strip() for p in raw.split("\n") if p.strip()]
            if parts:
                author_name = parts[0]
            for p in parts:
                if p.startswith("@"):
                    author_handle = p
                    break
        except Exception:
            pass

        # Timestamp
        created_at = None
        time_el = article.locator("time").first
        try:
            created_at = await time_el.get_attribute("datetime", timeout=2000)
        except Exception:
            pass

        # Metrics
        metrics = {}
        for key in ["reply", "retweet", "like", "bookmark"]:
            el = article.locator(f'[data-testid="{key}"]').first
            try:
                val = await el.get_attribute("aria-label", timeout=1000) or ""
                num_match = re.search(r"([\d,]+)", val)
                metrics[key + "s"] = num_match.group(1).replace(",", "") if num_match else "0"
            except Exception:
                metrics[key + "s"] = "0"

        # Retweet / quote context
        is_retweet = False
        try:
            ctx = article.locator('[data-testid="socialContext"]').first
            ctx_text = await ctx.inner_text(timeout=1000)
            is_retweet = "reposted" in ctx_text.lower() or "retweeted" in ctx_text.lower()
        except Exception:
            pass

        # Media
        has_media = False
        try:
            media = article.locator('[data-testid="tweetPhoto"], video').first
            await media.wait_for(state="attached", timeout=500)
            has_media = True
        except Exception:
            pass

        return {
            "id": tweet_id,
            "text": text,
            "author_name": author_name,
            "author_handle": author_handle,
            "created_at": created_at,
            "url": f"https://x.com{href}",
            "is_retweet": is_retweet,
            "has_media": has_media,
            "metrics": metrics,
        }

    except Exception:
        return None


# ---------------------------------------------------------------------------
# Timeline scraper
# ---------------------------------------------------------------------------

async def scrape_timeline(page: Page, max_tweets: int = 100) -> list[dict]:
    print(f"Scraping home timeline (target: {max_tweets} tweets)...")
    await page.goto("https://x.com/home", wait_until="domcontentloaded")
    await page.wait_for_timeout(3000)

    tweets: list[dict] = []
    seen_ids: set[str] = set()
    no_new_rounds = 0

    while len(tweets) < max_tweets and no_new_rounds < 8:
        articles = await page.locator('article[data-testid="tweet"]').all()
        new_this_round = 0

        for article in articles:
            tweet = await extract_tweet(article)
            if tweet and tweet["id"] not in seen_ids:
                seen_ids.add(tweet["id"])
                tweets.append(tweet)
                new_this_round += 1

        if new_this_round == 0:
            no_new_rounds += 1
        else:
            no_new_rounds = 0
            print(f"  {len(tweets)} tweets collected...")

        if len(tweets) < max_tweets:
            await page.evaluate("window.scrollBy(0, window.innerHeight * 3)")
            await page.wait_for_timeout(2500)

    print(f"  -> {len(tweets)} timeline tweets extracted.")
    return tweets[:max_tweets]


# ---------------------------------------------------------------------------
# Own tweets scraper
# ---------------------------------------------------------------------------

async def scrape_own_tweets(page: Page, username: str, max_tweets: int = 100) -> list[dict]:
    print(f"Scraping @{username}'s own tweets (target: {max_tweets})...")
    await page.goto(f"https://x.com/{username}", wait_until="domcontentloaded")
    await page.wait_for_timeout(3000)

    tweets: list[dict] = []
    seen_ids: set[str] = set()
    no_new_rounds = 0

    while len(tweets) < max_tweets and no_new_rounds < 8:
        articles = await page.locator('article[data-testid="tweet"]').all()
        new_this_round = 0

        for article in articles:
            tweet = await extract_tweet(article)
            if tweet and tweet["id"] not in seen_ids:
                seen_ids.add(tweet["id"])
                tweets.append(tweet)
                new_this_round += 1

        if new_this_round == 0:
            no_new_rounds += 1
        else:
            no_new_rounds = 0
            print(f"  {len(tweets)} tweets collected...")

        if len(tweets) < max_tweets:
            await page.evaluate("window.scrollBy(0, window.innerHeight * 3)")
            await page.wait_for_timeout(2500)

    print(f"  -> {len(tweets)} own tweets extracted.")
    return tweets[:max_tweets]


# ---------------------------------------------------------------------------
# Storage
# ---------------------------------------------------------------------------

def save_snapshot(tweets: list[dict], source: str, username: str):
    now = datetime.now(timezone.utc)
    date_str = now.strftime("%Y-%m-%d")
    ts_str = now.strftime("%Y%m%d_%H%M%S")

    day_dir = DATA_DIR / date_str
    day_dir.mkdir(parents=True, exist_ok=True)

    snapshot_file = day_dir / f"{source}_{ts_str}.json"
    snapshot = {
        "fetched_at": now.isoformat(),
        "source": source,
        "username": username,
        "count": len(tweets),
        "tweets": tweets,
    }
    with open(snapshot_file, "w", encoding="utf-8") as f:
        json.dump(snapshot, f, ensure_ascii=False, indent=2)

    rel = snapshot_file.relative_to(DATA_DIR.parent)
    print(f"  Saved {len(tweets)} tweets -> {rel}")
    _update_index(rel, snapshot)


def _update_index(relative_path: Path, snapshot: dict):
    index_file = DATA_DIR / "index.json"
    index = []
    if index_file.exists():
        with open(index_file, "r", encoding="utf-8") as f:
            index = json.load(f)
    index.append({
        "file": str(relative_path),
        "fetched_at": snapshot["fetched_at"],
        "source": snapshot["source"],
        "username": snapshot["username"],
        "count": snapshot["count"],
    })
    with open(index_file, "w", encoding="utf-8") as f:
        json.dump(index, f, ensure_ascii=False, indent=2)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

async def run(source: str, max_tweets: int):
    email = os.getenv("TWITTER_EMAIL")
    password = os.getenv("TWITTER_PASSWORD")
    username = os.getenv("TWITTER_USERNAME")

    if not email or not password or not username:
        print("ERROR: set TWITTER_EMAIL, TWITTER_PASSWORD, TWITTER_USERNAME in .env")
        sys.exit(1)

    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=True,
            args=["--no-sandbox", "--disable-setuid-sandbox", "--disable-dev-shm-usage"],
        )
        context = await browser.new_context(
            viewport={"width": 1280, "height": 900},
            user_agent=(
                "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36"
            ),
        )
        page = await context.new_page()

        await ensure_logged_in(page, context, email, password, username)

        if source in ("timeline", "both"):
            tweets = await scrape_timeline(page, max_tweets)
            save_snapshot(tweets, "timeline", username)

        if source in ("mine", "both"):
            tweets = await scrape_own_tweets(page, username, max_tweets)
            save_snapshot(tweets, "mine", username)

        await browser.close()

    print("\nDone. See data/ for the saved files.")


def main():
    parser = argparse.ArgumentParser(description="Fetch tweets via browser automation.")
    parser.add_argument(
        "--source",
        choices=["timeline", "mine", "both"],
        default="both",
        help="Which tweets to fetch (default: both)",
    )
    parser.add_argument(
        "--max",
        type=int,
        default=100,
        help="Max tweets per source (default: 100)",
    )
    args = parser.parse_args()
    asyncio.run(run(args.source, args.max))


if __name__ == "__main__":
    main()
