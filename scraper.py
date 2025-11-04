#!/usr/bin/env python3
"""
Ultimate Vegamovies Scraper (final)
- Uses PAGES env var (no input()) so it runs on Railway automatically
- Brave-style blocking, VGMLinkz-only clicks, multi-quality handling,
  top-priority host resolution, episode skipping, Telegram send.
"""

import os
import re
import json
import time
import logging
import requests
from datetime import datetime
from bs4 import BeautifulSoup
from playwright.sync_api import sync_playwright

# ---------- CONFIG ----------
BASE_DOMAIN = "https://vegamoviesog.city"
RESULT_FILE = os.path.join(os.getcwd(), "results.txt")
SCRAPED_FILE = os.path.join(os.getcwd(), "scraped.json")

# Priority of hostnames (top to bottom)
HOST_PRIORITY = ["gdtot", "gdflix", "hubcloud", "v-cloud", "xcloud", "filebee", "drive.google"]
VGML_KEYWORDS = ["vgml", "vgmlinkz", "vgmlinks"]
BLOCKED_DOMAINS = [
    "ads", "analytics", "doubleclick", "propellerads", "googletagmanager",
    "shortly", "techymovies", "extralinks", "ez4short", "vdrive", "popads"
]
EPISODE_PATTERNS = [r"\bep\b", r"\bep\.", r"\bepisode\b", r"\bs\d{1,2}e\d{1,2}\b", r"\bs\d{1,2}\b"]

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")

# ---------- Load scraped state ----------
try:
    with open(SCRAPED_FILE, "r", encoding="utf-8") as f:
        scraped = json.load(f)
except:
    scraped = {}

def save_scraped():
    with open(SCRAPED_FILE, "w", encoding="utf-8") as f:
        json.dump(scraped, f, indent=2, ensure_ascii=False)

# ---------- Cleaning & Parsing ----------
def clean_title(raw):
    if not raw:
        return "Unknown"
    s = re.sub(r"[._\-]+", " ", str(raw))
    s = re.sub(r"\.(mkv|mp4|avi|mov|webm)$", "", s, flags=re.I)
    s = re.sub(r"\b(download|web[- ]?dl|bluray|brrip|hdrip|dual audio|hindi|english|esub|subs|x264|x265|hevc|vegamovies|series|season)\b", "", s, flags=re.I)
    return re.sub(r"\s+", " ", s).strip().title()

def extract_quality(text):
    if not text:
        return "Unknown"
    m = re.search(r"(2160p|1080p|720p|480p)", str(text), re.I)
    return m.group(1) if m else "Unknown"

def is_episode(text):
    if not text:
        return False
    t = str(text).lower()
    return any(re.search(p, t, re.I) for p in EPISODE_PATTERNS)

# ---------- Telegram ----------
def send_to_telegram(token, chat_id, file_path):
    if not token or not chat_id:
        logging.warning("⚠️ Telegram not configured, skipping send.")
        return False
    if not os.path.exists(file_path):
        logging.warning("⚠️ results.txt not found, skipping Telegram send.")
        return False
    url = f"https://api.telegram.org/bot{token}/sendDocument"
    caption = f"🎬 Vegamovies Scrape — {datetime.utcnow().strftime('%Y-%m-%d %H:%M UTC')}"
    try:
        with open(file_path, "rb") as fp:
            resp = requests.post(url, data={"chat_id": chat_id, "caption": caption}, files={"document": fp}, timeout=120)
        if resp.status_code == 200:
            logging.info("✅ Sent results.txt to Telegram.")
            return True
        logging.error(f"❌ Telegram send failed: {resp.status_code} {resp.text}")
    except Exception as e:
        logging.error(f"❌ Telegram exception: {e}")
    return False

# ---------- Network blocking for Brave-like behavior ----------
def block_unwanted(route, request):
    url = request.url.lower()
    if any(b in url for b in BLOCKED_DOMAINS):
        return route.abort()
    return route.continue_()

# ---------- Helpers ----------
def get_movie_links(page_html):
    soup = BeautifulSoup(page_html, "html.parser")
    return [a["href"] for a in soup.select("h3.entry-title a") if a.get("href")]

def resolve_vgmlinkz(vgm, context):
    """Visit VGMLinkz, wait for JS, click any 'continue' buttons, extract final link across all tags."""
    vpage = context.new_page()
    try:
        vpage.goto(vgm, wait_until="domcontentloaded", timeout=90000)
        time.sleep(5)

        # Try to click through any shorteners twice if needed
        for _ in range(2):
            for txt in ["click here", "continue", "proceed", "get link", "get links"]:
                try:
                    btn = vpage.query_selector(f"text={txt}")
                    if btn:
                        btn.click()
                        logging.info(f"👉 Clicked '{txt}'")
                        time.sleep(5)
                except:
                    continue

        # wait again for dynamic content
        time.sleep(4)
        html = vpage.content()
        vsoup = BeautifulSoup(html, "html.parser")

        found_links = set()

        # Search 1️⃣ all attributes of all tags
        for tag in vsoup.find_all(True):
            for attr, val in tag.attrs.items():
                if isinstance(val, str) and any(h in val.lower() for h in HOST_PRIORITY):
                    found_links.add(val)

        # Search 2️⃣ plain <a href="">
        for a in vsoup.find_all("a", href=True):
            href = a["href"]
            if any(h in href.lower() for h in HOST_PRIORITY):
                found_links.add(href)

        # Search 3️⃣ onclick / onmouseover / data-link patterns
        for attr in ["onclick", "onmouseover", "data-href", "data-link", "data-url"]:
            for tag in vsoup.find_all(attrs={attr: True}):
                val = tag.get(attr, "")
                for m in re.findall(r"https?://[^\s'\"]+", val):
                    if any(h in m.lower() for h in HOST_PRIORITY):
                        found_links.add(m)

        # Search 4️⃣ meta refresh
        for meta in vsoup.find_all("meta", attrs={"http-equiv": True}):
            if meta.get("http-equiv", "").lower() == "refresh":
                m = re.search(r"url=(https?://[^\s'\"]+)", meta.get("content", ""), re.I)
                if m:
                    found_links.add(m.group(1))

        # Search 5️⃣ in script text (JSON blobs or JS redirects)
        for script in vsoup.find_all("script"):
            text = script.get_text()
            for m in re.findall(r"https?://[^\s'\"<>]+", text):
                if any(h in m.lower() for h in HOST_PRIORITY):
                    found_links.add(m)

        # Search 6️⃣ in visible text content (last resort)
        text_dump = vsoup.get_text(" ", strip=True)
        for m in re.findall(r"https?://[^\s'\"<>]+", text_dump):
            if any(h in m.lower() for h in HOST_PRIORITY):
                found_links.add(m)

        # Filter duplicates and episode-related junk
        clean_links = [l for l in found_links if not is_episode(l)]

        # Prioritize by host order
        for host in HOST_PRIORITY:
            for link in clean_links:
                if host in link.lower():
                    logging.info(f"✅ Found {host.upper()} link: {link}")
                    return link

        logging.warning(f"⚠️ No valid links found in {vgm}")
        return None

    except Exception as e:
        logging.error(f"❌ Error processing VGMLinkz {vgm}: {e}")
        return None
    finally:
        try:
            vpage.close()
        except:
            pass

def save_result(title, quality, link):
    line = f"{title}  {quality}  {link}"
    with open(RESULT_FILE, "a", encoding="utf-8") as f:
        f.write(line + "\n")
    logging.info(f"💾 Saved: {line}")

def process_movie(movie_url, context):
    if movie_url in scraped:
        logging.info(f"⏭️ Skipping already scraped: {movie_url}")
        return

    logging.info(f"🎬 Visiting movie page: {movie_url}")
    page = context.new_page()
    try:
        page.goto(movie_url, wait_until="domcontentloaded", timeout=60000)
        time.sleep(4)
        soup = BeautifulSoup(page.content(), "html.parser")

        # Collect only VGMLinkz hrefs (strict)
        vgms = []
        for a in soup.find_all("a", href=True):
            href = a["href"]
            text = (a.get_text(" ", strip=True) or "").lower()
            if not any(k in href.lower() for k in VGML_KEYWORDS):
                continue
            # skip junk links (how to, join, report etc.)
            if any(bad in text for bad in ["how to", "join", "telegram", "report", "watch", "broken", "tutorial"]):
                continue
            vgms.append(href)

        if not vgms:
            logging.warning(f"⚠️ No VGMLinkz buttons found on: {movie_url}")
            scraped[movie_url] = True
            save_scraped()
            return

        # dedupe while preserving order
        seen = set()
        vgms_unique = []
        for x in vgms:
            if x not in seen:
                seen.add(x)
                vgms_unique.append(x)

        title_page_text = page.title() or ""
        title_clean = clean_title(title_page_text)

        # Many pages list multiple qualities in the page text; capture quality labels near buttons if possible
        page_text = soup.get_text(" ", strip=True).lower()
        qualities_found = re.findall(r"(2160p|1080p|720p|480p)", page_text, re.I)
        qualities = []
        if qualities_found:
            # keep unique in order
            for q in qualities_found:
                qn = q.lower()
                if qn not in qualities:
                    qualities.append(qn)
        else:
            qualities = ["unknown"]

        # For each VGMLinkz link: resolve final host link and save once per quality (avoid duplicates)
        saved_qualities = set()
        for vgm in vgms_unique:
            final = resolve_vgmlinkz(vgm, context)
            if not final:
                continue

            # determine quality: try to find quality string near this link in page HTML (best-effort)
            # fallback to qualities list order
            qual = "unknown"
            # search the movie page HTML for the vgm href and nearby quality text
            html = str(soup)
            idx = html.find(vgm)
            if idx != -1:
                snippet = html[max(0, idx-200): idx+200].lower()
                m = re.search(r"(2160p|1080p|720p|480p)", snippet, re.I)
                if m:
                    qual = m.group(1).lower()
            if qual == "unknown" and qualities:
                # pick first not-saved quality
                for q in qualities:
                    if q not in saved_qualities:
                        qual = q
                        break

            if qual in saved_qualities:
                # already have this quality saved, skip
                continue

            save_result(title_clean, qual, final)
            saved_qualities.add(qual)

            # if we have seen all qualities found on page, we can stop early
            if qualities and saved_qualities.issuperset(set(qualities)):
                break

        scraped[movie_url] = True
        save_scraped()

    except Exception as e:
        logging.error(f"❌ Error processing {movie_url}: {e}")
    finally:
        try:
            page.close()
        except:
            pass

def run_scraper(pages):
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True, args=["--no-sandbox"])
        context = browser.new_context()
        context.route("**/*", block_unwanted)

        for page_num in pages:
            list_url = f"{BASE_DOMAIN}/page/{page_num}/"
            logging.info(f"📄 Scraping page {page_num}: {list_url}")
            page = context.new_page()
            try:
                page.goto(list_url, wait_until="domcontentloaded", timeout=60000)
                time.sleep(3)
                movie_links = get_movie_links(page.content())
                logging.info(f"🔗 Found {len(movie_links)} posts on page {page_num}")
                for movie_url in movie_links:
                    process_movie(movie_url, context)
            except Exception as e:
                logging.error(f"⚠️ Failed list page {page_num}: {e}")
            finally:
                try:
                    page.close()
                except:
                    pass

        try:
            browser.close()
        except:
            pass

# ---------- CLI / Entrypoint ----------
def parse_pages(pagestr):
    pagestr = pagestr.strip()
    if "-" in pagestr:
        a, b = map(int, pagestr.split("-", 1))
        if a > b:
            a, b = b, a
        return list(range(a, b + 1))
    return [int(pagestr)]

if __name__ == "__main__":
    # Use PAGES environment variable (Railway). default to 1-2 when not set.
    pages_input = os.environ.get("PAGES", "1-2").strip()
    pages = parse_pages(pages_input)
    logging.info(f"🚀 Starting scraper for pages: {pages}")

    run_scraper(pages)

    # Send results to Telegram if configured (env vars) - safe: no input() used
    BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
    CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")
    if BOT_TOKEN and CHAT_ID:
        send_to_telegram(BOT_TOKEN, CHAT_ID, RESULT_FILE)
    else:
        logging.info("ℹ️ TELEGRAM_BOT_TOKEN or TELEGRAM_CHAT_ID not set — skipping Telegram send.")

    logging.info("✅ Scraper finished.")
