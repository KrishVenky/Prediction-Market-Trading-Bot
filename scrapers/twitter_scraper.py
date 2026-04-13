"""
Twitter Profile Scraper
-----------------------
Scrapes recent tweets and profile info from Twitter profiles
WITHOUT using the Twitter API or any paid services.

Strategy (in order of preference):
  1. Nitter via Playwright — solves bot-protection challenges, parses clean HTML
  2. Twitter.com via Playwright — direct scrape with stealth browser
  3. Nitter via requests — plain HTTP (works only if instance has no bot-check)

Usage:
  python scraper.py elonmusk sama
  python scraper.py @naval @paulg --max-tweets 30 --csv
  python scraper.py elonmusk --method twitter
"""

import argparse
import csv
import json
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Optional

import requests
from bs4 import BeautifulSoup

# ── Config ─────────────────────────────────────────────────────────────────

NITTER_INSTANCES = [
    "https://nitter.tiekoetter.com",
    "https://nitter.poast.org",
    "https://nitter.privacydev.net",
    "https://nitter.1d4.us",
    "https://nitter.kavin.rocks",
    "https://nitter.moomoo.me",
    "https://nitter.weiler.rocks",
]

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "en-US,en;q=0.9",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
}

REQUEST_TIMEOUT = 12
DELAY_BETWEEN_PROFILES = 2.0  # seconds — polite delay between users


# ─────────────────────────────────────────────────────────────────────────────
# HTML Parsers (Nitter)
# ─────────────────────────────────────────────────────────────────────────────

def _text(el) -> str:
    return el.get_text(strip=True) if el else ""


def parse_profile_nitter(soup: BeautifulSoup, username: str) -> dict:
    profile: dict = {"username": username}
    profile["display_name"] = _text(soup.select_one(".profile-card-fullname"))
    profile["bio"] = _text(soup.select_one(".profile-bio"))

    stats: dict = {}
    for li in soup.select(".profile-stat"):
        key_el = li.select_one(".profile-stat-header")
        val_el = li.select_one(".profile-stat-num")
        if key_el and val_el:
            key = _text(key_el).lower().replace(" ", "_")
            val = _text(val_el).replace(",", "")
            stats[key] = val
    profile["stats"] = stats
    profile["location"] = _text(soup.select_one(".profile-location"))

    url_el = soup.select_one(".profile-website a")
    profile["website"] = url_el["href"] if url_el else ""
    profile["joined"] = _text(soup.select_one(".profile-joindate")).replace("Joined", "").strip()
    return profile


def parse_tweet_nitter(item) -> dict:
    tweet: dict = {}
    tweet["text"] = _text(item.select_one(".tweet-content"))
    link_el = item.select_one(".tweet-date a")
    if link_el:
        tweet["time_display"] = link_el.get("title", "")
        tweet["tweet_url_path"] = link_el.get("href", "")

    for stat in item.select(".tweet-stat"):
        icon_el = stat.select_one(".icon-container")
        icon_class = " ".join(icon_el.get("class", [])) if icon_el else ""
        val = _text(stat).strip() or "0"
        if "comment" in icon_class or "reply" in icon_class:
            tweet["replies"] = val
        elif "retweet" in icon_class:
            tweet["retweets"] = val
        elif "heart" in icon_class or "like" in icon_class:
            tweet["likes"] = val

    tweet["is_retweet"] = bool(item.select_one(".retweet-header"))
    tweet["images"] = [img["src"] for img in item.select(".attachment img") if img.get("src")]
    return tweet


def parse_html_nitter(html: str, username: str, base_url: str, max_tweets: int) -> Optional[dict]:
    """Parse a full Nitter profile HTML page."""
    soup = BeautifulSoup(html, "html.parser")

    # Reject bot-check pages
    title = soup.title.string if soup.title else ""
    if "bot" in title.lower() or "captcha" in title.lower() or "challenge" in title.lower():
        return None

    error_el = soup.select_one(".error-panel")
    if error_el:
        print(f"  ✗  Nitter error: {_text(error_el)}")
        return None

    profile = parse_profile_nitter(soup, username)
    profile["source_url"] = f"{base_url}/{username}"
    profile["scraped_at"] = datetime.utcnow().isoformat() + "Z"

    tweets = []
    for item in soup.select(".timeline-item:not(.show-more)"):
        tweet = parse_tweet_nitter(item)
        if tweet.get("text"):
            tweet["tweet_url"] = base_url + tweet.get("tweet_url_path", "")
            tweets.append(tweet)
        if len(tweets) >= max_tweets:
            break

    if not profile.get("display_name") and not tweets:
        return None  # Empty page — likely blocked

    profile["tweets"] = tweets
    profile["tweet_count_fetched"] = len(tweets)
    return profile


# ─────────────────────────────────────────────────────────────────────────────
# Strategy 1 — Nitter via Playwright (solves JS challenges)
# ─────────────────────────────────────────────────────────────────────────────

def scrape_nitter_playwright(username: str, max_tweets: int = 20) -> Optional[dict]:
    """Use Playwright to load a Nitter instance (handles Anubis/bot challenges)."""
    try:
        from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout
    except ImportError:
        return None

    with sync_playwright() as pw:
        browser = pw.chromium.launch(
            headless=True,
            args=["--no-sandbox", "--disable-blink-features=AutomationControlled"],
        )
        context = browser.new_context(
            viewport={"width": 1280, "height": 900},
            user_agent=HEADERS["User-Agent"],
            locale="en-US",
            java_script_enabled=True,
        )
        page = context.new_page()

        for base in NITTER_INSTANCES:
            url = f"{base}/{username}"
            try:
                print(f"  ⟳  Nitter+Playwright → {url}")
                page.goto(url, wait_until="domcontentloaded", timeout=20_000)

                # Wait for either profile or timeline to appear (up to 15s)
                # This gives the Anubis PoW time to complete
                try:
                    page.wait_for_selector(
                        ".profile-card, .timeline-item, .error-panel",
                        timeout=15_000,
                    )
                except PWTimeout:
                    print(f"  ✗  Timed out waiting for content on {base}")
                    continue

                html = page.content()
                result = parse_html_nitter(html, username, base, max_tweets)
                if result:
                    result["method"] = "nitter_playwright"
                    browser.close()
                    return result
                else:
                    print(f"  ✗  No usable data from {base}")

            except PWTimeout:
                print(f"  ✗  Page load timeout on {base}")
            except Exception as exc:
                print(f"  ✗  {base} → {exc}")

        browser.close()
    return None


# ─────────────────────────────────────────────────────────────────────────────
# Strategy 2 — Twitter.com via Playwright
# ─────────────────────────────────────────────────────────────────────────────

def scrape_twitter_playwright(username: str, max_tweets: int = 20) -> Optional[dict]:
    """Headless-browser scrape of twitter.com directly."""
    try:
        from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout
    except ImportError:
        print("  ✗  Playwright not installed. Run:")
        print("       pip install playwright")
        print("       playwright install chromium")
        return None

    print(f"  ⟳  Twitter.com Playwright → @{username}")
    profile_url = f"https://twitter.com/{username}"

    with sync_playwright() as pw:
        browser = pw.chromium.launch(
            headless=True,
            args=[
                "--no-sandbox",
                "--disable-blink-features=AutomationControlled",
                "--disable-web-security",
            ],
        )
        context = browser.new_context(
            viewport={"width": 1280, "height": 900},
            user_agent=HEADERS["User-Agent"],
            locale="en-US",
            extra_http_headers={"Accept-Language": "en-US,en;q=0.9"},
        )
        # Remove navigator.webdriver flag
        context.add_init_script("""
            Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
        """)
        page = context.new_page()

        try:
            page.goto(profile_url, wait_until="domcontentloaded", timeout=30_000)
            page.wait_for_selector('[data-testid="primaryColumn"]', timeout=25_000)
        except PWTimeout:
            print(f"  ✗  Twitter.com timed out for @{username}")
            browser.close()
            return None
        except Exception as exc:
            print(f"  ✗  Twitter.com error for @{username}: {exc}")
            browser.close()
            return None

        # Scroll to load more tweets
        for _ in range(4):
            page.keyboard.press("End")
            page.wait_for_timeout(1500)

        html = page.content()
        browser.close()

    return parse_html_twitter(html, username)


def parse_html_twitter(html: str, username: str) -> Optional[dict]:
    """Parse Twitter.com HTML (rendered by Playwright)."""
    soup = BeautifulSoup(html, "html.parser")

    profile: dict = {
        "username": username,
        "source_url": f"https://twitter.com/{username}",
        "scraped_at": datetime.utcnow().isoformat() + "Z",
        "method": "twitter_playwright",
    }

    name_el = soup.select_one('[data-testid="UserName"]')
    if name_el:
        spans = name_el.find_all("span")
        profile["display_name"] = _text(spans[0]) if spans else _text(name_el)
    else:
        profile["display_name"] = ""

    bio_el = soup.select_one('[data-testid="UserDescription"]')
    profile["bio"] = _text(bio_el)

    location_el = soup.select_one('[data-testid="UserLocation"]')
    profile["location"] = _text(location_el)

    url_el = soup.select_one('[data-testid="UserUrl"] a')
    profile["website"] = url_el["href"] if url_el else ""

    joined_el = soup.select_one('[data-testid="UserJoinDate"]')
    profile["joined"] = _text(joined_el).replace("Joined", "").strip()

    # Follower / following counts
    stats: dict = {}
    for a in soup.select('a[href$="/following"], a[href$="/followers"], a[href$="/verified_followers"]'):
        text = _text(a)
        parts = text.split()
        if len(parts) >= 2:
            stats[parts[-1].lower()] = parts[0]
    profile["stats"] = stats

    # Tweets
    tweets = []
    for article in soup.select('[data-testid="tweet"]'):
        tweet: dict = {}
        txt_el = article.select_one('[data-testid="tweetText"]')
        tweet["text"] = _text(txt_el)
        if not tweet["text"]:
            continue

        time_el = article.select_one("time")
        tweet["time_display"] = time_el.get("datetime", "") if time_el else ""

        link_el = article.select_one('a[href*="/status/"]')
        tweet["tweet_url"] = "https://twitter.com" + link_el["href"] if link_el else ""

        for stat, testid in [("replies", "reply"), ("retweets", "retweet"), ("likes", "like")]:
            stat_el = article.select_one(f'[data-testid="{testid}"] [data-testid]')
            tweet[stat] = _text(stat_el) or "0"

        ctx_el = article.select_one('[data-testid="socialContext"]')
        tweet["is_retweet"] = bool(ctx_el) and "Retweeted" in _text(ctx_el)
        tweet["images"] = [
            img["src"]
            for img in article.select('img[src*="pbs.twimg.com/media"]')
        ]
        tweets.append(tweet)
        if len(tweets) >= 20:
            break

    profile["tweets"] = tweets
    profile["tweet_count_fetched"] = len(tweets)

    if not profile.get("display_name") and not tweets:
        return None
    return profile


# ─────────────────────────────────────────────────────────────────────────────
# Strategy 3 — Nitter via plain requests (fastest, rarely works now)
# ─────────────────────────────────────────────────────────────────────────────

def scrape_nitter_requests(username: str, max_tweets: int = 20) -> Optional[dict]:
    """Plain HTTP request to Nitter — fast but often blocked."""
    for base in NITTER_INSTANCES:
        url = f"{base}/{username}"
        try:
            r = requests.get(url, headers=HEADERS, timeout=REQUEST_TIMEOUT)
            if r.status_code != 200:
                continue
            result = parse_html_nitter(r.text, username, base, max_tweets)
            if result:
                result["method"] = "nitter_requests"
                return result
        except Exception:
            pass
    return None


# ─────────────────────────────────────────────────────────────────────────────
# Output Helpers
# ─────────────────────────────────────────────────────────────────────────────

def save_json(data: list[dict], path: Path):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    print(f"\n✔  JSON saved → {path}")


def save_csv(data: list[dict], path: Path):
    rows = []
    for profile in data:
        base = {k: v for k, v in profile.items() if k not in ("tweets", "stats")}
        base["followers"] = profile.get("stats", {}).get("followers", "")
        base["following"] = profile.get("stats", {}).get("following", "")
        for tweet in profile.get("tweets", []):
            row = {**base, **tweet}
            row["images"] = "|".join(tweet.get("images", []))
            rows.append(row)

    if not rows:
        print("  ⚠  No tweets to write to CSV.")
        return

    fieldnames = list(rows[0].keys())
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)
    print(f"✔  CSV  saved → {path}")


def print_summary(profile: dict):
    sep = "─" * 64
    method_tag = f"[{profile.get('method', '?')}]"
    print(f"\n{sep}")
    print(f"  @{profile['username']}  —  {profile.get('display_name', '')}  {method_tag}")
    print(sep)
    if profile.get("bio"):
        print(f"  Bio      : {profile['bio'][:120]}")
    if profile.get("location"):
        print(f"  Location : {profile['location']}")
    if profile.get("joined"):
        print(f"  Joined   : {profile['joined']}")
    stats = profile.get("stats", {})
    if stats:
        stat_str = "  |  ".join(f"{k}: {v}" for k, v in stats.items())
        print(f"  Stats    : {stat_str}")
    print(f"  Tweets   : {profile.get('tweet_count_fetched', 0)} fetched  |  {profile.get('scraped_at', '')}")
    print(sep)

    for i, tw in enumerate(profile.get("tweets", []), 1):
        rt_tag = "[RT] " if tw.get("is_retweet") else ""
        text = tw.get("text", "")[:110].replace("\n", " ")
        likes = tw.get("likes", "0")
        rts = tw.get("retweets", "0")
        ts = tw.get("time_display", "")
        print(f"  {i:>2}. {rt_tag}{text}")
        print(f"      ♥ {likes}  ↺ {rts}  |  {ts}")
        if tw.get("images"):
            print(f"      🖼  {len(tw['images'])} image(s)")
        print()


# ─────────────────────────────────────────────────────────────────────────────
# Main Orchestration
# ─────────────────────────────────────────────────────────────────────────────

METHODS = {
    "auto": ["nitter_requests", "nitter_playwright", "twitter"],
    "nitter": ["nitter_requests", "nitter_playwright"],
    "twitter": ["twitter"],
    "playwright": ["nitter_playwright", "twitter"],
}


def scrape_profile(username: str, max_tweets: int, method: str) -> Optional[dict]:
    chain = METHODS.get(method, METHODS["auto"])
    for strategy in chain:
        profile = None
        if strategy == "nitter_requests":
            profile = scrape_nitter_requests(username, max_tweets)
        elif strategy == "nitter_playwright":
            profile = scrape_nitter_playwright(username, max_tweets)
        elif strategy == "twitter":
            profile = scrape_twitter_playwright(username, max_tweets)

        if profile:
            return profile
    return None


def main():
    parser = argparse.ArgumentParser(
        description="Twitter profile scraper — no API, no payment required",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Methods:
  auto      — try plain Nitter first, then Nitter+Playwright, then twitter.com (default)
  nitter    — Nitter only (requests → Playwright)
  twitter   — twitter.com via Playwright directly
  playwright — skip plain requests, use Playwright for both Nitter & twitter.com

Examples:
  python scraper.py elonmusk sama
  python scraper.py @naval @paulg --max-tweets 30 --csv
  python scraper.py elonmusk --method twitter
  python scraper.py elonmusk --no-save
        """,
    )
    parser.add_argument("usernames", nargs="+", help="Twitter username(s) to scrape")
    parser.add_argument("--max-tweets", type=int, default=20, metavar="N",
                        help="Max tweets per profile (default: 20)")
    parser.add_argument("--output-dir", default="output", metavar="DIR",
                        help="Directory for output files (default: output/)")
    parser.add_argument("--csv", action="store_true",
                        help="Also export as CSV (one row per tweet)")
    parser.add_argument("--method", default="auto",
                        choices=["auto", "nitter", "twitter", "playwright"],
                        help="Scraping method (default: auto)")
    parser.add_argument("--no-save", action="store_true",
                        help="Print results only, do not write files")
    args = parser.parse_args()

    output_dir = Path(args.output_dir)
    if not args.no_save:
        output_dir.mkdir(parents=True, exist_ok=True)

    print(f"\n{'═'*64}")
    print(f"  Twitter Profile Scraper  |  {len(args.usernames)} account(s)  |  method={args.method}")
    print(f"{'═'*64}")

    results = []
    for username in args.usernames:
        username = username.lstrip("@").strip()
        print(f"\n⟳  Scraping @{username}…")
        profile = scrape_profile(username, args.max_tweets, args.method)
        if profile:
            print_summary(profile)
            results.append(profile)
        else:
            print(f"  ✗  Could not scrape @{username} — all strategies failed.")
        time.sleep(DELAY_BETWEEN_PROFILES)

    if not results:
        print("\n✗  No profiles scraped.\n")
        sys.exit(1)

    print(f"\n{'═'*64}")
    print(f"  Result: {len(results)}/{len(args.usernames)} profile(s) scraped successfully")
    print(f"{'═'*64}")

    if not args.no_save:
        ts = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
        save_json(results, output_dir / f"twitter_scrape_{ts}.json")
        if args.csv:
            save_csv(results, output_dir / f"twitter_scrape_{ts}.csv")

    print()


if __name__ == "__main__":
    main()
