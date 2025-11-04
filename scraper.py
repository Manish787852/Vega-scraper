#!/usr/bin/env python3
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
HOST_PRIORITY = ["gdtot", "gdflix", "hubcloud", "v-cloud", "drive.google"]
BUTTON_PRIORITY = ["batch", "zip", "v-cloud", "vcloud", "download"]
EPISODE_PATTERNS = [r"\bep\b", r"\bep\.", r"\bepisode\b", r"\bs\d{1,2}e\d{1,2}\b"]

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")

# ---------- LOAD SCRAPED ----------
try:
    with open(SCRAPED_FILE, "r", encoding="utf-8") as f:
        scraped = json.load(f)
except:
    scraped = {}

def save_scraped():
    with open(SCRAPED_FILE, "w", encoding="utf-8") as f:
        json.dump(scraped, f, indent=2, ensure_ascii=False)

# ---------- UTILITIES ----------
def is_episode(text):
    if not text:
        return False
    return any(re.search(p, text, re.I) for p in EPISODE_PATTERNS)

def clean_title(raw):
    if not raw:
        return "Unknown"
    s = re.sub(r"\.(mkv|mp4|avi|mov|webm)$", "", raw)
    s = re.sub(r"[._\-]+", " ", s)
    junk = [
        "download", "bluray", "brrip", "hdrip", "dual audio", "hin", "eng", "english", "hindi",
        "esub", "subs", "x264", "x265", "10bit", "720p", "480p", "1080p", "2160p", "web", "series",
        "season", "vegamovies", "to"
    ]
    for w in junk:
        s = re.sub(rf"\b{re.escape(w)}\b", "", s, flags=re.I)
    s = re.sub(r"\s+", " ", s).strip()
    return s.title()

def extract_quality(text):
    if not text:
        return "Unknown"
    m = re.search(r"(2160p|1080p|720p|480p)", text, re.I)
    return m.group(1).lower() if m else "Unknown"

def send_to_telegram(token, chat_id, file_path):
    if not token or not chat_id or not os.path.exists(file_path):
        logging.warning("⚠️ Missing Telegram info or results file.")
        return
    url = f"https://api.telegram.org/bot{token}/sendDocument"
    caption = f"🎬 Scrape results — {datetime.utcnow().strftime('%Y-%m-%d %H:%M UTC')}"
    try:
        with open(file_path, "rb") as f:
            files = {"document": (os.path.basename(file_path), f)}
            data = {"chat_id": chat_id, "caption": caption}
            r = requests.post(url, data=data, files=files, timeout=60)
        if r.status_code == 200:
            logging.info("✅ Sent results.txt to Telegram.")
        else:
            logging.error(f"❌ Telegram send failed: {r.text}")
    except Exception as e:
        logging.error(f"❌ Telegram error: {e}")

# ---------- SCRAPER ----------
def process_movie(movie_url, context):
    if movie_url in scraped:
        logging.info(f"⏭️ Skipping already scraped: {movie_url}")
        return

    logging.info(f"🎬 Processing: {movie_url}")
    page = context.new_page()
    try:
        page.goto(movie_url, wait_until="domcontentloaded", timeout=60000)
        time.sleep(3)
        soup = BeautifulSoup(page.content(), "html.parser")
        title = clean_title(page.title())

        # find quality sections
        text_content = soup.get_text(" ", strip=True)
        found_qualities = re.findall(r"(2160p|1080p|720p|480p)", text_content, re.I)
        qualities = sorted(set([q.lower() for q in found_qualities]), reverse=True) or ["unknown"]

        # find all valid download buttons
        a_tags = soup.find_all("a", href=True)
        buttons = []
        for a in a_tags:
            text = a.get_text(" ", strip=True).lower()
            href = a["href"]
            if any(k in text for k in BUTTON_PRIORITY) and not is_episode(text):
                buttons.append((text, href))

        if not buttons:
            logging.warning(f"⚠️ No valid buttons in {movie_url}")
            scraped[movie_url] = True
            save_scraped()
            return

        # group by quality
        for quality in qualities:
            selected = None
            for priority in BUTTON_PRIORITY:
                for text, href in buttons:
                    if priority in text and quality in text:
                        selected = href
                        break
                if selected:
                    break
            # if quality not in button text, pick first priority available
            if not selected:
                for priority in BUTTON_PRIORITY:
                    for text, href in buttons:
                        if priority in text:
                            selected = href
                            break
                    if selected:
                        break

            if selected:
                logging.info(f"➡️ {title} [{quality}] -> {selected}")
                real_link = resolve_vgmlinks(selected, context)
                if real_link:
                    save_result(title, quality, real_link)
            else:
                logging.warning(f"❌ No link found for {quality}")

    except Exception as e:
        logging.error(f"❌ Failed {movie_url}: {e}")
    finally:
        scraped[movie_url] = True
        save_scraped()
        page.close()

def resolve_vgmlinks(vgm_url, context):
    try:
        vpage = context.new_page()
        vpage.goto(vgm_url, wait_until="domcontentloaded", timeout=60000)
        time.sleep(4)
        vsoup = BeautifulSoup(vpage.content(), "html.parser")
        for host in HOST_PRIORITY:
            for a in vsoup.find_all("a", href=True):
                href = a["href"]
                if host in href and not is_episode(href):
                    logging.info(f"✅ Resolved {host}: {href}")
                    vpage.close()
                    return href
        vpage.close()
    except Exception as e:
        logging.warning(f"⚠️ VGMLinkz failed {vgm_url}: {e}")
    return None

def save_result(title, quality, link):
    with open(RESULT_FILE, "a", encoding="utf-8") as f:
        f.write(f"{title}  {quality}  {link}\n")
    logging.info(f"💾 Saved: {title}  {quality}  {link}")

def run_scraper(pages):
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True, args=["--no-sandbox"])
        context = browser.new_context(user_agent="Mozilla/5.0")
        for pg in pages:
            list_url = f"{BASE_DOMAIN}/page/{pg}/"
            logging.info(f"📄 Scraping list page {list_url}")
            page = context.new_page()
            try:
                page.goto(list_url, wait_until="domcontentloaded", timeout=60000)
                time.sleep(3)
                soup = BeautifulSoup(page.content(), "html.parser")
                posts = [a["href"] for a in soup.select("h3.entry-title a")]
                logging.info(f"🔗 Found {len(posts)} posts")
                for post in posts:
                    process_movie(post, context)
            except Exception as e:
                logging.error(f"⚠️ Error on page {pg}: {e}")
            finally:
                page.close()
        browser.close()

def parse_pages(pagestr):
    pagestr = pagestr.strip()
    if "-" in pagestr:
        a, b = map(int, pagestr.split("-"))
        return list(range(a, b + 1))
    return [int(pagestr)]

if __name__ == "__main__":
    pagestr = os.environ.get("PAGES", input("Enter page number or range (e.g. 1 or 14-20): "))
    pages = parse_pages(pagestr)
    logging.info(f"🚀 Running scraper for pages {pages}")
    run_scraper(pages)
    logging.info("✅ Done scraping. Sending results.txt to Telegram...")
    send_to_telegram(os.environ.get("TELEGRAM_BOT_TOKEN"), os.environ.get("TELEGRAM_CHAT_ID"), RESULT_FILE)
