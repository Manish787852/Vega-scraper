#!/usr/bin/env python3
"""
Final VegaMovies Scraper (No shortener resolution)
Works like Brave adblock — blocks JS shorteners, extracts only real GDTOT/GDFlix/Drive/VCloud links
Environment variables (for Railway):
  PAGES=1-5
  BASE_DOMAIN=https://vegamoviesog.city
  TELEGRAM_BOT_TOKEN=xxxx
  TELEGRAM_CHAT_ID=xxxx
"""

import os
import re
import json
import time
import logging
from datetime import datetime
from bs4 import BeautifulSoup
from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout
import requests

# ---------------- CONFIG ----------------
BASE_DOMAIN = os.environ.get("BASE_DOMAIN", "https://vegamoviesog.city").rstrip("/")
RESULT_FILE = "results.txt"
SCRAPED_FILE = "scraped.json"

HOST_PRIORITY = ["gdtot", "gdflix", "hubcloud", "v-cloud", "drive.google", "gdrive", "gdlink"]
ACCEPT_BUTTON_KEYWORDS = ["batch", "zip", "download", "v-cloud", "vcloud", "download now"]
BLOCK_PATTERNS_SUBSTR = [
    "m.vdrive", "short", "clicksfly", "shrink", "adfly", "adsby", "trk.", "tracking", "analytics",
    "adservice", "googlesyndication", "boost.ink", "ouo.io", "cutt.ly", "mdrive"
]

PAGE_LOAD_RETRIES = 3
MOVIE_LOAD_RETRIES = 3
VGMLINK_LOAD_RETRIES = 3
WAIT_ANCHORS_SECONDS = 7
HEADLESS = True  # True for Railway
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")

# ---------------- UTIL ----------------
def safe_load_json(path):
    try:
        if os.path.exists(path):
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
    except Exception:
        pass
    return {}

scraped = safe_load_json(SCRAPED_FILE)

def save_scraped():
    with open(SCRAPED_FILE, "w", encoding="utf-8") as f:
        json.dump(scraped, f, indent=2, ensure_ascii=False)

def write_result(title, quality, link):
    with open(RESULT_FILE, "a", encoding="utf-8") as f:
        f.write(f"{title}  {quality}  {link}\n")
    logging.info(f"✅ Saved: {title}  {quality}  {link}")

def extract_quality(text):
    m = re.search(r"(2160p|1080p|720p|480p|360p)", text or "", re.I)
    return m.group(1).lower() if m else "unknown"

def clean_title(raw):
    if not raw: return "Unknown"
    s = re.sub(r"[\[\]\(\)\{\}]", "", raw)
    s = re.sub(r"\b(download|bluray|hindi|english|dual audio|esubs?|webrip|hdrip|web-dl)\b", "", s, flags=re.I)
    s = re.sub(r"\s+", " ", s).strip()
    return s.title()

def prefer_links(links):
    def score(h):
        low = h.lower()
        for i, host in enumerate(HOST_PRIORITY):
            if host in low: return i
        return len(HOST_PRIORITY)
    return sorted(links, key=score)

# ---------------- CORE ----------------
def run_scraper(page_range):
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=HEADLESS, args=["--no-sandbox"])
        context = browser.new_context()

        # Block ads/shorteners/scripts
        def route_handler(route):
            url = route.request.url.lower()
            if any(x in url for x in BLOCK_PATTERNS_SUBSTR):
                try: route.abort()
                except: pass
            else:
                try: route.continue_()
                except: pass
        context.route("**/*", route_handler)

        page = context.new_page()

        for page_num in page_range:
            list_url = f"{BASE_DOMAIN}/page/{page_num}/"
            logging.info(f"📄 Scraping list page: {list_url}")
            try:
                page.goto(list_url, wait_until="domcontentloaded", timeout=60000)
                soup = BeautifulSoup(page.content(), "html.parser")
                movie_links = [a["href"] for a in soup.select("h3.entry-title a[href]")]
            except Exception as e:
                logging.warning(f"⚠️ Failed list page: {e}")
                continue

            for movie_url in movie_links:
                if scraped.get(movie_url): continue
                process_movie(context, movie_url)
                scraped[movie_url] = True
                save_scraped()

        browser.close()

def process_movie(context, movie_url):
    logging.info(f"🎬 Processing movie: {movie_url}")
    page = context.new_page()
    try:
        page.goto(movie_url, wait_until="domcontentloaded", timeout=60000)
        soup = BeautifulSoup(page.content(), "html.parser")
        vgmlinks = []
        for a in soup.find_all("a", href=True):
            href = a["href"].strip()
            txt = a.get_text(" ", strip=True).lower()
            if ("vgml" in href or "vgmlink" in href) and any(k in txt for k in ACCEPT_BUTTON_KEYWORDS):
                vgmlinks.append(href)
        if not vgmlinks:
            logging.warning(f"⚠️ No VGMLink found on {movie_url}")
            return

        for vgm in vgmlinks:
            logging.info(f"➡️ Visiting VGMLink: {vgm}")
            extract_from_vgmlink(context, vgm)
    except Exception as e:
        logging.error(f"❌ Movie failed: {e}")
    finally:
        page.close()

def extract_from_vgmlink(context, vgm_url):
    page = context.new_page()
    try:
        page.goto(vgm_url, wait_until="domcontentloaded", timeout=60000)
        time.sleep(2)
        soup = BeautifulSoup(page.content(), "html.parser")
        found = []
        for a in soup.find_all("a", href=True):
            href = a["href"]
            if not href.startswith("http"): continue
            href_low = href.lower()
            # Only accept real download hosts, skip shorteners
            if any(x in href_low for x in HOST_PRIORITY):
                found.append(href)
        if not found:
            logging.warning(f"⚠️ No valid links found: {vgm_url}")
            return
        found = prefer_links(found)
        title = clean_title(soup.title.string if soup.title else "Unknown")
        for link in found:
            q = extract_quality(link)
            write_result(title, q, link)
    except Exception as e:
        logging.warning(f"⚠️ VGMLink failed {vgm_url}: {e}")
    finally:
        page.close()

# ---------------- TELEGRAM ----------------
def send_results(bot, chat, file):
    if not (bot and chat): return
    try:
        url = f"https://api.telegram.org/bot{bot}/sendDocument"
        with open(file, "rb") as f:
            requests.post(url, data={"chat_id": chat}, files={"document": f})
        logging.info("📨 Sent to Telegram")
    except Exception as e:
        logging.error(f"Telegram error: {e}")

# ---------------- MAIN ----------------
def parse_pages_input(s):
    s = s.strip()
    if "-" in s:
        a, b = map(int, s.split("-"))
        return list(range(a, b + 1))
    return [int(s)]

if __name__ == "__main__":
    pages = parse_pages_input(os.environ.get("PAGES", "1"))
    logging.info(f"Running scraper for pages: {pages}")
    run_scraper(pages)
    send_results(os.environ.get("TELEGRAM_BOT_TOKEN"), os.environ.get("TELEGRAM_CHAT_ID"), RESULT_FILE)
    logging.info("✅ Done.")
