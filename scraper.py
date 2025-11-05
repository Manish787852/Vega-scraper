import os
import re
import json
import time
import logging
import asyncio
from playwright.sync_api import sync_playwright
from bs4 import BeautifulSoup
import requests

# ---------------- CONFIG ----------------
BASE_URL = "https://vegamoviesog.city"
RESULT_FILE = "results.txt"
SCRAPED_FILE = "scraped.json"

# Priority for host links
HOST_PRIORITY = ["gdtot", "gdflix", "gdrive", "v-cloud"]

# Buttons to accept on movie pages
ACCEPT_BUTTON_KEYWORDS = ["download", "batch", "zip", "v-cloud"]

# Telegram Config (from Railway variables)
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "")

# Logging
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")

# ---------------- UTILITIES ----------------
def load_scraped():
    if os.path.exists(SCRAPED_FILE):
        with open(SCRAPED_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return {}

def save_scraped():
    with open(SCRAPED_FILE, "w", encoding="utf-8") as f:
        json.dump(scraped, f, indent=2)

def clean_title(text):
    text = re.sub(r"\b(download|bluray|web-dl|webdl|zip|complete|episode|hindi|english|dual audio|subs|x264|x265|10bit|hevc|hdrip|brrip|rip|pack)\b", "", text, flags=re.I)
    text = re.sub(r"\s+", " ", text).strip()
    return text.title()

def extract_quality(text):
    match = re.search(r"(2160p|1080p|720p|480p)", text, re.I)
    return match.group(1) if match else "Unknown"

def is_episode(text):
    return bool(re.search(r"episode|ep[ .-]?\d", text, re.I))

def send_to_telegram(token, chat_id, filepath):
    if not token or not chat_id:
        logging.warning("⚠️ Telegram token/chat_id not set — skipping send.")
        return
    with open(filepath, "rb") as f:
        files = {"document": f}
        data = {"chat_id": chat_id}
        resp = requests.post(f"https://api.telegram.org/bot{token}/sendDocument", data=data, files=files)
        if resp.status_code == 200:
            logging.info("📤 Results sent to Telegram successfully.")
        else:
            logging.error(f"❌ Telegram upload failed: {resp.text}")

# ---------------- VGMLINKZ RESOLVER ----------------
def resolve_vgmlinkz(vgm, context):
    """Visits VGMLinkz and extracts the real download link."""
    vpage = context.new_page()
    try:
        vpage.goto(vgm, wait_until="domcontentloaded", timeout=90000)
        time.sleep(4)

        # Try bypass buttons (shorteners)
        for label in ["click here", "continue", "proceed", "get link", "get links"]:
            try:
                btn = vpage.query_selector(f"text={label}")
                if btn:
                    btn.click()
                    logging.info(f"🖱️ Clicked '{label}' button")
                    time.sleep(4)
            except Exception:
                continue

        # Wait for host links
        for host in HOST_PRIORITY:
            selector = f"a[href*='{host}']"
            try:
                vpage.wait_for_selector(selector, timeout=15000)
                href = vpage.eval_on_selector(selector, "el => el.href")
                if href and not is_episode(href):
                    logging.info(f"✅ Found {host.upper()} link: {href}")
                    return href
            except Exception:
                continue

        # Check all anchors if still nothing
        for a in vpage.query_selector_all("a"):
            try:
                href = a.get_attribute("href")
                if href and any(h in href for h in HOST_PRIORITY) and not is_episode(href):
                    logging.info(f"✅ Found backup host link: {href}")
                    return href
            except:
                continue

        logging.warning(f"⚠️ No valid link found in {vgm}")
        return None

    except Exception as e:
        logging.error(f"❌ VGMLinkz error: {e}")
        return None
    finally:
        vpage.close()

# ---------------- MOVIE PAGE PARSER ----------------
def process_movie(movie_url, context):
    if movie_url in scraped:
        logging.info(f"⏭️ Skipping already scraped: {movie_url}")
        return

    logging.info(f"🎬 Processing: {movie_url}")
    page = context.new_page()
    try:
        page.goto(movie_url, wait_until="domcontentloaded", timeout=90000)
        time.sleep(4)
        soup = BeautifulSoup(page.content(), "html.parser")

        # Collect VGMLinkz buttons only (not episodes)
        vgmlinks = []
        for a in soup.find_all("a", href=True):
            href = a["href"]
            text = a.get_text(" ", strip=True).lower()
            if ("vgml" in href or "vgmlinkz" in href) and not is_episode(text) and any(k in text for k in ACCEPT_BUTTON_KEYWORDS):
                vgmlinks.append(href)

        if not vgmlinks:
            logging.warning(f"⚠️ No VGMLinkz buttons found in {movie_url}")
            scraped[movie_url] = True
            save_scraped()
            return

        # Process VGMLinkz buttons
        for vgm in vgmlinks:
            logging.info(f"➡️ Visiting VGMLinkz: {vgm}")
            final_link = resolve_vgmlinkz(vgm, context)
            if final_link:
                raw_title = soup.title.get_text() if soup.title else movie_url
                title = clean_title(raw_title)
                quality = extract_quality(raw_title)
                with open(RESULT_FILE, "a", encoding="utf-8") as f:
                    f.write(f"{title}  {quality}  {final_link}\n")
                logging.info(f"✅ Saved: {title}  {quality}  {final_link}")

    except Exception as e:
        logging.error(f"❌ Failed {movie_url}: {e}")
    finally:
        scraped[movie_url] = True
        save_scraped()
        page.close()

# ---------------- SCRAPER RUNNER ----------------
def run_scraper(pages):
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True, args=["--no-sandbox"])
        context = browser.new_context()

        # 🚫 Manual adblock (mimic Brave)
        ADBLOCK_PATTERNS = [
            "*://*.adbull.org/*", "*://*.mdrive.lol/*", "*://*.shrinkme.io/*",
            "*://*.boost.ink/*", "*://*.shorteners.dev/*", "*://*.rekonise.com/*"
        ]
        for pattern in ADBLOCK_PATTERNS:
            context.route(pattern, lambda route: route.abort())

        for page_num in pages:
            list_url = f"{BASE_URL}/page/{page_num}/"
            logging.info(f"🌍 Scraping list page: {list_url}")

            page = context.new_page()
            page.goto(list_url, wait_until="domcontentloaded", timeout=90000)
            time.sleep(4)
            soup = BeautifulSoup(page.content(), "html.parser")
            movie_links = [a["href"] for a in soup.select("a[href*='/download-'], a[href*='/the-']")]

            logging.info(f"🎞️ Found {len(movie_links)} movie links")
            for mlink in movie_links:
                process_movie(mlink, context)
            page.close()

        browser.close()

# ---------------- MAIN ----------------
def parse_pages(pagestr):
    pagestr = pagestr.strip()
    if "-" in pagestr:
        a, b = map(int, pagestr.split("-"))
        return list(range(a, b + 1))
    return [int(pagestr)]

if __name__ == "__main__":
    scraped = load_scraped()
    pages_input = os.environ.get("PAGES", "1-2")
    pages = parse_pages(pages_input)

    logging.info(f"🚀 Running scraper for pages: {pages}")
    run_scraper(pages)
    logging.info("✅ Scraping finished, sending results.txt to Telegram...")
    send_to_telegram(TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID, RESULT_FILE)
