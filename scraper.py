#!/usr/bin/env python3
"""
✅ VegaMovies Full Auto Scraper (Playwright version)
- Visits every movie/series page
- Clicks only VGMLinkz buttons
- Fetches highest-priority links (GDToT > GDFlix > HubCloud > V-Cloud > Drive)
- Handles multiple qualities and seasons
- Skips episode-wise links
- Cleans titles
- Saves results.txt and scraped.json
- Sends results.txt to Telegram after run
"""

import os, re, json, time, logging, requests
from datetime import datetime
from bs4 import BeautifulSoup
from playwright.sync_api import sync_playwright

# ---------- CONFIG ----------
BASE_DOMAIN = "https://vegamoviesog.city"
RESULT_FILE = os.path.join(os.getcwd(), "results.txt")
SCRAPED_FILE = os.path.join(os.getcwd(), "scraped.json")

HOST_PRIORITY = ["gdtot", "gdflix", "hubcloud", "v-cloud", "drive.google"]
VGML_KEYWORDS = ["vgml", "vgmlinkz", "vgmlinks"]
EPISODE_PATTERNS = [r"\bep\b", r"\bep\.", r"\bepisode\b", r"\bs\d{1,2}e\d{1,2}\b"]

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")

# ---------- Load scraped ----------
try:
    with open(SCRAPED_FILE, "r", encoding="utf-8") as f:
        scraped = json.load(f)
except:
    scraped = {}

def save_scraped():
    with open(SCRAPED_FILE, "w", encoding="utf-8") as f:
        json.dump(scraped, f, indent=2, ensure_ascii=False)

# ---------- Clean title ----------
def clean_title(text):
    s = re.sub(r"[._\-]+", " ", text)
    s = re.sub(r"\.(mkv|mp4|avi|mov)$", "", s, flags=re.I)
    junk = [
        "download", "bluray", "web-dl", "webdl", "hdrip", "brrip",
        "dual audio", "hindi", "english", "esubs", "subs", "x264", "x265",
        "hevc", "vegamovies", "series", "season"
    ]
    for j in junk:
        s = re.sub(rf"\b{j}\b", "", s, flags=re.I)
    s = re.sub(r"\s+", " ", s).strip()
    return s.title()

def extract_quality(text):
    match = re.search(r"(2160p|1080p|720p|480p)", text, re.I)
    return match.group(1).lower() if match else "unknown"

def is_episode(text):
    for p in EPISODE_PATTERNS:
        if re.search(p, text, re.I):
            return True
    return False

# ---------- Telegram ----------
def send_to_telegram(bot_token, chat_id, file_path):
    if not bot_token or not chat_id or not os.path.exists(file_path):
        return
    url = f"https://api.telegram.org/bot{bot_token}/sendDocument"
    with open(file_path, "rb") as f:
        requests.post(url, data={"chat_id": chat_id, "caption": "🎬 Scrape Results"}, files={"document": f})

# ---------- Main Scraping ----------
def run_scraper(pages):
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True, args=["--no-sandbox"])
        context = browser.new_context()
        page = context.new_page()

        for page_num in pages:
            list_url = f"{BASE_DOMAIN}/page/{page_num}/"
            logging.info(f"📄 Scraping list page: {list_url}")

            try:
                page.goto(list_url, wait_until="domcontentloaded", timeout=60000)
                time.sleep(2)
                soup = BeautifulSoup(page.content(), "html.parser")
                links = [a["href"] for a in soup.select("h3.entry-title a") if a.get("href")]
                logging.info(f"🔗 Found {len(links)} movie links")

                for movie_url in links:
                    process_movie(movie_url, context)
            except Exception as e:
                logging.error(f"⚠️ Failed list page {page_num}: {e}")

        browser.close()

def process_movie(movie_url, context):
    if movie_url in scraped:
        logging.info(f"⏭️ Skipping already scraped: {movie_url}")
        return

    logging.info(f"🎬 Processing movie page: {movie_url}")
    page = context.new_page()

    try:
        page.goto(movie_url, wait_until="domcontentloaded", timeout=60000)
        time.sleep(2)
        soup = BeautifulSoup(page.content(), "html.parser")

        # ✅ Collect only VGMLinkz buttons
        vgms = []
        for a in soup.find_all("a", href=True):
            text = a.get_text(" ", strip=True).lower()
            href = a["href"]

            if not any(k in href for k in VGML_KEYWORDS):
                continue
            if any(bad in text for bad in ["how to", "join", "telegram", "report", "watch", "broken"]):
                continue
            vgms.append(href)

        if not vgms:
            logging.warning(f"⚠️ No VGMLinkz found: {movie_url}")
            scraped[movie_url] = True
            save_scraped()
            return

        # Deduplicate links
        vgms = list(dict.fromkeys(vgms))

        for vgm in vgms:
            logging.info(f"➡️ Visiting VGMLinkz: {vgm}")
            vpage = context.new_page()
            try:
                vpage.goto(vgm, wait_until="domcontentloaded", timeout=60000)
                time.sleep(3)
                vsoup = BeautifulSoup(vpage.content(), "html.parser")

                final_links = []
                for host in HOST_PRIORITY:
                    for a in vsoup.find_all("a", href=True):
                        href = a["href"]
                        if host in href and not is_episode(href):
                            final_links.append(href)
                    if final_links:
                        break  # only top-priority host group

                if not final_links:
                    logging.warning(f"⚠️ No valid final links: {vgm}")
                    continue

                # take only one link per quality
                seen_qualities = set()
                for final in final_links:
                    q = extract_quality(final)
                    if q in seen_qualities:
                        continue
                    seen_qualities.add(q)

                    fpage = context.new_page()
                    try:
                        fpage.goto(final, wait_until="domcontentloaded", timeout=60000)
                        time.sleep(2)
                        fsoup = BeautifulSoup(fpage.content(), "html.parser")
                        h5 = fsoup.find("h5", class_=re.compile("m-0"))
                        raw = h5.get_text(strip=True) if h5 else fpage.title()
                        title = clean_title(raw)
                        quality = extract_quality(raw)
                        with open(RESULT_FILE, "a", encoding="utf-8") as f:
                            f.write(f"{title}  {quality}  {final}\n")
                        logging.info(f"✅ Saved: {title}  {quality}  {final}")
                    finally:
                        fpage.close()
            except Exception as e:
                logging.warning(f"⚠️ VGMLinkz error: {e}")
            finally:
                vpage.close()

    except Exception as e:
        logging.error(f"❌ Failed {movie_url}: {e}")
    finally:
        scraped[movie_url] = True
        save_scraped()
        page.close()

# ---------- Run ----------
def parse_pages(pagestr):
    pagestr = pagestr.strip()
    if "-" in pagestr:
        a, b = map(int, pagestr.split("-"))
        return list(range(a, b + 1))
    return [int(pagestr)]

if __name__ == "__main__":
    pagestr = os.environ.get("PAGES", "1-2")
    pages = parse_pages(pagestr)
    logging.info(f"🚀 Running scraper for pages: {pages}")
    run_scraper(pages)
    send_to_telegram(os.environ.get("TELEGRAM_BOT_TOKEN"), os.environ.get("TELEGRAM_CHAT_ID"), RESULT_FILE)
    logging.info("✅ Done.")
