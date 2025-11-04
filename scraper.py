#!/usr/bin/env python3
"""
Railway Vegamovies scraper — fully automated
Scrapes all movie pages (Batch/Zip/Download/V-Cloud), cleans titles, saves results.txt,
and sends the file to Telegram.

Environment variables required:
  TELEGRAM_BOT_TOKEN
  TELEGRAM_CHAT_ID
"""
import os
import subprocess

# Ensure Playwright Chromium is installed
try:
    subprocess.run(["playwright", "install", "--with-deps", "chromium"], check=True)
except Exception as e:
    print("Playwright install failed:", e)
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
RESULT_FILE = "results.txt"
SCRAPED_FILE = "scraped.json"
HOST_PRIORITY = ["gdtot", "gdflix", "hubcloud", "v-cloud", "drive.google"]
ACCEPT_BUTTON_KEYWORDS = ["batch", "zip", "download", "v-cloud", "vcloud", "v cloud"]
EPISODE_PATTERNS = [r"\bep\b", r"\bep\.", r"\bepisode\b", r"\bs\d{1,2}e\d{1,2}\b"]

# ---------- LOGGING ----------
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")

# ---------- LOAD SCRAPED MEMORY ----------
if os.path.exists(SCRAPED_FILE):
    with open(SCRAPED_FILE, "r", encoding="utf-8") as f:
        scraped = json.load(f)
else:
    scraped = {}

def save_scraped():
    with open(SCRAPED_FILE, "w", encoding="utf-8") as f:
        json.dump(scraped, f, indent=2, ensure_ascii=False)

# ---------- CLEANING ----------
def clean_title(raw):
    if not raw:
        return "Unknown"
    s = re.sub(r"\.(mkv|mp4|avi|mov|webm)$", "", raw, flags=re.I)
    s = re.sub(r"[._\-]+", " ", s)
    junk = [
        "download", "web-dl", "bluray", "brrip", "hdrip", "dual audio",
        "hin", "eng", "english", "hindi", "esub", "subs", "x264", "x265",
        "10bit", "720p", "480p", "1080p", "2160p", "web", "series",
        "season", "vegamovies", "to"
    ]
    for j in junk:
        s = re.sub(rf"\b{j}\b", "", s, flags=re.I)
    s = re.sub(r"\s+", " ", s).strip()
    return s.title()

def extract_quality(text):
    if not text:
        return "Unknown"
    q = re.search(r"(2160p|1080p|720p|480p)", text, re.I)
    return q.group(1).lower() if q else "Unknown"

def is_episode(text):
    if not text:
        return False
    for pat in EPISODE_PATTERNS:
        if re.search(pat, text, re.I):
            return True
    return False

# ---------- TELEGRAM ----------
def send_to_telegram(bot_token, chat_id, file_path):
    if not os.path.exists(file_path):
        logging.warning("⚠️ No results.txt to send.")
        return
    url = f"https://api.telegram.org/bot{bot_token}/sendDocument"
    with open(file_path, "rb") as f:
        data = {"chat_id": chat_id, "caption": "🎬 New Vegamovies Scrape Results"}
        files = {"document": (os.path.basename(file_path), f)}
        r = requests.post(url, data=data, files=files)
    if r.status_code == 200:
        logging.info("✅ Sent results.txt to Telegram.")
    else:
        logging.error(f"❌ Telegram send failed: {r.text}")

# ---------- SCRAPER ----------
def run_scraper(pages):
    open(RESULT_FILE, "a", encoding="utf-8").close()
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True, args=["--no-sandbox"])
        context = browser.new_context()
        page = context.new_page()

        for pnum in pages:
            list_url = f"{BASE_DOMAIN}/page/{pnum}/"
            logging.info(f"📄 Page {pnum}: {list_url}")
            try:
                page.goto(list_url, wait_until="domcontentloaded", timeout=60000)
                time.sleep(2)
                soup = BeautifulSoup(page.content(), "html.parser")
                posts = [a["href"] for a in soup.select("h3.entry-title a")]
                logging.info(f"🔗 Found {len(posts)} movies.")
                for link in posts:
                    process_movie(link, context)
            except Exception as e:
                logging.warning(f"⚠️ Failed page {pnum}: {e}")

        browser.close()

def process_movie(movie_url, context):
    if movie_url in scraped:
        logging.info(f"⏭️ Skipping already scraped: {movie_url}")
        return

    logging.info(f"🎬 Processing: {movie_url}")
    page = context.new_page()

    try:
        page.goto(movie_url, wait_until="domcontentloaded", timeout=60000)
        time.sleep(2)
        soup = BeautifulSoup(page.content(), "html.parser")

        # ✅ Only pick VGMLinkz or main batch/zip download buttons (ignore episode links)
        vgms = []
        for a in soup.find_all("a", href=True):
            text = a.get_text(" ", strip=True).lower()
            href = a["href"]

            # Accept only VGMLinkz or batch/zip/full download links
            if (
                ("vgml" in href or "vgmlinkz" in href)
                or any(k in text for k in ["batch", "zip", "download now", "full", "v-cloud"])
            ) and not is_episode(text):
                vgms.append(href)

        if not vgms:
            logging.warning(f"⚠️ No valid VGMLinkz or batch links found in {movie_url}")
            scraped[movie_url] = True
            save_scraped()
            return

        # ✅ Visit each VGMLinkz page
        for vgm in vgms:
            logging.info(f"➡️ Visiting VGMLinkz: {vgm}")
            vpage = context.new_page()

            try:
                vpage.goto(vgm, wait_until="domcontentloaded", timeout=60000)
                time.sleep(3)
                vsoup = BeautifulSoup(vpage.content(), "html.parser")

                # Collect only prioritized links
                final_links = []
                for host in HOST_PRIORITY:
                    for a in vsoup.find_all("a", href=True):
                        href = a["href"]
                        if host in href and not is_episode(href):
                            final_links.append(href)

                if not final_links:
                    continue

                # ✅ Extract final link details
                for final in final_links:
                    fpage = context.new_page()
                    try:
                        fpage.goto(final, wait_until="domcontentloaded", timeout=60000)
                        time.sleep(2)
                        fsoup = BeautifulSoup(fpage.content(), "html.parser")

                        # Clean title + quality
                        h5 = fsoup.find("h5", class_=re.compile("m-0"))
                        raw = h5.get_text(strip=True) if h5 else fpage.title()
                        title = clean_title(raw)
                        quality = extract_quality(raw)

                        # ✅ Save to results.txt
                        with open(RESULT_FILE, "a", encoding="utf-8") as f:
                            f.write(f"{title}  {quality}  {final}\n")

                        logging.info(f"✅ Saved: {title}  {quality}  {final}")

                    finally:
                        fpage.close()
            finally:
                vpage.close()

    except Exception as e:
        logging.error(f"❌ Failed {movie_url}: {e}")

    finally:
        scraped[movie_url] = True
        save_scraped()
        page.close()

def parse_pages(pagestr):
    pagestr = pagestr.strip()
    if "-" in pagestr:
        a, b = map(int, pagestr.split("-"))
        return list(range(a, b + 1))
    return [int(pagestr)]

if __name__ == "__main__":
    pages_input = os.environ.get("PAGES", "1-2")  # Default range if Railway variable not set
    print(f"📄 Running scraper for pages: {pages_input}")
    pages = parse_pages(pages_input)
    run_scraper(pages)
    logging.info("✅ Scraping done. Sending results.txt to Telegram...")
    send_to_telegram(os.environ.get("TELEGRAM_BOT_TOKEN"), os.environ.get("TELEGRAM_CHAT_ID"), RESULT_FILE)
