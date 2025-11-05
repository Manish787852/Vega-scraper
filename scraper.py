#!/usr/bin/env python3
"""
Playwright-based Vegamovies scraper (production-ready)

Usage (Railway / CI style):
 - Set env vars:
    PAGES="14-20"              # or "20" or "1"
    BASE_DOMAIN="https://vegamoviesog.city"  # optional, default used otherwise
    TELEGRAM_BOT_TOKEN
    TELEGRAM_CHAT_ID
 - Worker command: python scraper.py

Outputs:
 - results.txt   (Title  Quality  Link)  (two spaces between fields)
 - scraped.json  (movie_url -> true)
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

# ---------- CONFIG ----------
BASE_DOMAIN = os.environ.get("BASE_DOMAIN", "https://vegamoviesog.city")
RESULT_FILE = os.environ.get("RESULT_FILE", "results.txt")
SCRAPED_FILE = os.environ.get("SCRAPED_FILE", "scraped.json")
# host priority - order we prefer (we'll still collect all qualities)
HOST_PRIORITY = ["gdtot", "gdflix", "hubcloud", "v-cloud", "drive.google", "gdlink", "gdlink.dev", "gdrive"]
ACCEPT_BUTTON_KEYWORDS = ["batch", "zip", "download", "v-cloud", "vcloud", "v cloud", "download now", "g-direct", "g-direct"]
EPISODE_INDICATORS = [r"\bep\b", r"\bep\.", r"\bepisode\b", r"\bs\d{1,2}e\d{1,2}\b", r"\bs\d{1,2}\b", r"each ep", r"each ep!"]
PAGE_LOAD_RETRIES = 3
MOVIE_LOAD_RETRIES = 3
VGMLINK_LOAD_RETRIES = 3
FINAL_LOAD_RETRIES = 2
HEADLESS = True   # Railway: keep True. Locally you can set False for debugging.

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")

# ---------- utilities ----------
def safe_load_json(path):
    try:
        if os.path.exists(path):
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
    except Exception:
        logging.exception("Could not read JSON, starting empty.")
    return {}

scraped = safe_load_json(SCRAPED_FILE)

def save_scraped():
    with open(SCRAPED_FILE, "w", encoding="utf-8") as f:
        json.dump(scraped, f, indent=2, ensure_ascii=False)

def write_result_line(title, quality, link):
    # format: Title  Quality  Link  (two spaces between fields as you requested)
    line = f"{title}  {quality}  {link}"
    with open(RESULT_FILE, "a", encoding="utf-8") as f:
        f.write(line + "\n")
    logging.info(f"✅ Saved: {line}")

# ---------- text helpers ----------
def is_episode(text):
    if not text:
        return False
    t = text.lower()
    for pat in EPISODE_INDICATORS:
        if re.search(pat, t):
            return True
    return False

def clean_title(raw):
    if not raw:
        return "Unknown"
    s = raw.strip()
    # remove file extensions
    s = re.sub(r"\.(mkv|mp4|avi|mov|webm|zip)$", "", s, flags=re.I)
    # remove bracketed content except year
    def _brackets_remove(m):
        inner = m.group(1)
        if re.match(r"^\s*(19|20)\d{2}\s*$", inner):
            return f"({inner})"
        return ""
    s = re.sub(r"\[(.*?)\]", "", s)
    s = re.sub(r"\((.*?)\)", _brackets_remove, s)
    # replace separators with spaces and collapse whitespace
    s = re.sub(r"[._\-]+", " ", s)
    # remove common junk tokens
    junk = [r"\bdownload\b", r"\bweb[- ]?dl\b", r"\bbluray\b", r"\bbrrip\b", r"\bhdrip\b",
            r"\bdual audio\b", r"\bhindi\b", r"\benglish\b", r"\besub\b", r"\bsubs\b",
            r"\bx264\b", r"\bx265\b", r"\b10bit\b", r"\b720p\b", r"\b480p\b", r"\b1080p\b",
            r"\b2160p\b", r"\bvegamovies\b", r"\bvegamovies.to\b", r"\bvegamovies.to\b"]
    for pat in junk:
        s = re.sub(pat, "", s, flags=re.I)
    s = re.sub(r"\s+", " ", s).strip()
    # Title case, but keep acronyms uppercase (simple approach)
    return " ".join([w.upper() if len(w) <= 3 else w.capitalize() for w in s.split()])

def extract_quality(text):
    if not text:
        return "Unknown"
    m = re.search(r"(2160p|1080p|720p|480p|360p)", text, re.I)
    return m.group(1).lower() if m else "Unknown"

def prefer_link_by_priority(links):
    """Given a list of hrefs, return list ordered by HOST_PRIORITY presence (keeps all but sorts)."""
    def score(h):
        h_low = h.lower()
        for i, host in enumerate(HOST_PRIORITY):
            if host in h_low:
                return - (len(HOST_PRIORITY) - i)  # prefer earlier (higher negative)
        return 0
    return sorted(links, key=lambda x: (score(x), x))

# ---------- network helpers (requests fallback) ----------
def fetch_text_requests(url, timeout=20):
    try:
        r = requests.get(url, timeout=timeout, headers={"User-Agent":"Mozilla/5.0"})
        return r.text
    except Exception as e:
        logging.debug(f"requests failed for {url}: {e}")
        return None

# ---------- scraping logic (Playwright) ----------
def run_scraper(page_list):
    # ensure results file exists
    open(RESULT_FILE, "a", encoding="utf-8").close()

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=HEADLESS, args=["--no-sandbox", "--disable-dev-shm-usage"])
        context = browser.new_context(user_agent="Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120 Safari/537.36")
        main_page = context.new_page()

        for page_num in page_list:
            list_url = f"{BASE_DOMAIN}/page/{page_num}/"
            logging.info(f"\n📄 Scraping list page: {list_url}")
            page_html = None
            for attempt in range(1, PAGE_LOAD_RETRIES + 1):
                try:
                    main_page.goto(list_url, wait_until="domcontentloaded", timeout=45000)
                    time.sleep(1.2)
                    page_html = main_page.content()
                    break
                except PWTimeout as e:
                    logging.warning(f"⚠️ Timeout loading page {list_url} (attempt {attempt})")
                except Exception as e:
                    logging.warning(f"⚠️ Error loading page {list_url} (attempt {attempt}): {e}")
            if not page_html:
                # try requests fallback
                logging.info("Trying requests fallback for list page...")
                page_html = fetch_text_requests(list_url)
                if not page_html:
                    logging.error(f"❌ Could not fetch list page {list_url}")
                    continue

            soup = BeautifulSoup(page_html, "html.parser")
            anchors = soup.select('h3.entry-title a')
            movie_links = []
            for a in anchors:
                href = a.get("href")
                if href:
                    movie_links.append(href)
            logging.info(f"🔗 Found {len(movie_links)} posts on page {page_num}")

            for movie_url in movie_links:
                # skip if already processed
                if scraped.get(movie_url):
                    logging.info(f"⏭️ Skipping already scraped: {movie_url}")
                    continue
                process_movie(movie_url, context)

        try:
            main_page.close()
        except:
            pass
        browser.close()
    logging.info("✅ Scrape finished.")

def process_movie(movie_url, context):
    logging.info(f"🎬 Processing movie page: {movie_url}")
    page = context.new_page()
    page_html = None
    for attempt in range(1, MOVIE_LOAD_RETRIES + 1):
        try:
            page.goto(movie_url, wait_until="domcontentloaded", timeout=45000)
            time.sleep(1.2)
            page_html = page.content()
            break
        except PWTimeout:
            logging.warning(f"⚠️ Timeout opening movie page (attempt {attempt}): {movie_url}")
        except Exception as e:
            logging.warning(f"⚠️ Error opening movie page (attempt {attempt}): {e}")
    if not page_html:
        page_html = fetch_text_requests(movie_url)
        if not page_html:
            logging.error(f"❌ Cannot fetch movie page {movie_url}, marking scraped to avoid loops.")
            scraped[movie_url] = True
            save_scraped()
            try: page.close()
            except: pass
            return

    soup = BeautifulSoup(page_html, "html.parser")
    # collect candidate VGMLINKS/direct batch links (anchors with buttons or text)
    candidate = []
    for a in soup.find_all("a", href=True):
        href = a["href"]
        text = (a.get_text(" ", strip=True) or "").lower()
        # only accept anchors with allowed keywords and not episode-like
        if (("vgml" in href.lower() or "vgmlink" in href.lower() or "vgmlinks" in href.lower()) and any(k in text for k in ACCEPT_BUTTON_KEYWORDS) and not is_episode(text)):
            candidate.append(href)
            continue
        # or if anchor text contains accept keywords (and not episode)
        if any(k in text for k in ACCEPT_BUTTON_KEYWORDS) and not is_episode(text):
            # only accept if href is not mailto or javascript
            if href.startswith("http"):
                candidate.append(href)

    # dedupe preserving order
    seen = set()
    vgm_links = []
    for h in candidate:
        if h not in seen:
            seen.add(h)
            vgm_links.append(h)

    if not vgm_links:
        logging.warning(f"⚠️ No Batch/Zip/Download/V-Cloud (VGMLINKS) found on {movie_url}. Marking scraped.")
        scraped[movie_url] = True
        save_scraped()
        try: page.close()
        except: pass
        return

    # For per-movie dedupe of title+quality
    saved_pairs = set()

    # process each VGMLINK / direct link
    for vgm in vgm_links:
        logging.info(f"➡️ Visiting VGMLINK/direct: {vgm}")
        vpage = context.new_page()
        vcontent = None
        for attempt in range(1, VGMLINK_LOAD_RETRIES + 1):
            try:
                vpage.goto(vgm, wait_until="domcontentloaded", timeout=45000)
                time.sleep(1.0)
                vcontent = vpage.content()
                break
            except PWTimeout:
                logging.warning(f"⚠️ Timeout loading VGMLINK ({attempt}): {vgm}")
            except Exception as e:
                logging.warning(f"⚠️ Error loading VGMLINK ({attempt}): {e}")
        if not vcontent:
            vcontent = fetch_text_requests(vgm)
            if not vcontent:
                logging.warning(f"⚠️ Could not fetch VGMLINK page: {vgm}")
                try: vpage.close()
                except: pass
                continue

        vsoup = BeautifulSoup(vcontent, "html.parser")
        # Collect all anchors that look like final hosts
        final_candidates = []
        for a in vsoup.find_all("a", href=True):
            href = a["href"]
            href_low = href.lower()
            # skip javascript/mailto and same-page anchors
            if not href.startswith("http"):
                continue
            # skip clearly episode-like links
            if is_episode(a.get_text(" ", strip=True) or href):
                continue
            # accept if host pattern in href OR anchor contains "gdtot/gdflix/hubcloud/drive"
            if any(h in href_low for h in HOST_PRIORITY) or any(k in (a.get_text(" ", strip=True).lower()) for k in ACCEPT_BUTTON_KEYWORDS):
                final_candidates.append(href)

        # final dedupe
        final_candidates = list(dict.fromkeys(final_candidates))

        if not final_candidates:
            logging.warning(f"⚠️ No final host links found on VGMLINK: {vgm}")
            try: vpage.close()
            except: pass
            continue

        # Prefer ordering by priority, but we keep all so we can extract multiple qualities
        final_candidates = prefer_link_by_priority(final_candidates)

        # For each final link, try to extract title & quality (prefer file page h5)
        for final in final_candidates:
            # avoid duplicates title+quality inside this movie
            # We'll attempt to obtain raw text from (1) final page's <h5 class="m-0 ..."> (2) final page title (3) vgmlink title
            raw_title = None
            quality = "Unknown"
            for attempt in range(1, FINAL_LOAD_RETRIES + 1):
                try:
                    fpage = context.new_page()
                    fpage.goto(final, wait_until="domcontentloaded", timeout=30000)
                    time.sleep(1.0)
                    fcontent = fpage.content()
                    fsoup = BeautifulSoup(fcontent, "html.parser")
                    # common pattern in your sample: <h5 class="m-0 font-weight-bold" align="center">The.New.World.2005.720p.BluRay...</h5>
                    h5 = fsoup.find("h5", class_=re.compile(r"m-0|font-weight-bold", re.I))
                    if h5 and h5.get_text(strip=True):
                        raw_title = h5.get_text(" ", strip=True)
                    else:
                        # fallback to title tag of final page
                        tit = fpage.title()
                        raw_title = tit if tit else raw_title
                    fpage.close()
                    break
                except Exception as e:
                    logging.debug(f"Final page load attempt {attempt} failed for {final}: {e}")
                    try:
                        fpage.close()
                    except:
                        pass
                    # try requests fallback for final page
                    txt = fetch_text_requests(final)
                    if txt:
                        fsoup = BeautifulSoup(txt, "html.parser")
                        h5 = fsoup.find("h5", class_=re.compile(r"m-0|font-weight-bold", re.I))
                        if h5 and h5.get_text(strip=True):
                            raw_title = h5.get_text(" ", strip=True)
                            break
            # if still no raw_title, fallback to vgmlink page title
            if not raw_title:
                # try vgmlink page title
                vtitle_tag = vsoup.find("h1", class_=re.compile(r"entry-title|entry-title|headline", re.I))
                if vtitle_tag and vtitle_tag.get_text(strip=True):
                    raw_title = vtitle_tag.get_text(" ", strip=True)
                else:
                    raw_title = movie_url.split("/")[-2].replace("-", " ")

            quality = extract_quality(raw_title)
            title = clean_title(raw_title)

            pair_key = f"{title}||{quality}"
            if pair_key in saved_pairs:
                logging.info(f"⏭️ Already saved this title+quality: {title} {quality}")
                continue

            # Save line
            write_result_line(title, quality, final)
            saved_pairs.add(pair_key)

        try:
            vpage.close()
        except:
            pass

    # Mark movie as scraped after processing all vgm links
    scraped[movie_url] = True
    save_scraped()
    try:
        page.close()
    except:
        pass

# ---------- Telegram upload ----------
def send_results_to_telegram(bot_token, chat_id, file_path):
    if not bot_token or not chat_id:
        logging.warning("Telegram token/chat not provided. Skipping send.")
        return False
    if not os.path.exists(file_path):
        logging.warning("Results file not found, nothing to send.")
        return False
    url = f"https://api.telegram.org/bot{bot_token}/sendDocument"
    caption = f"Scrape results — {datetime.utcnow().strftime('%Y-%m-%d %H:%M UTC')}"
    try:
        with open(file_path, "rb") as fp:
            files = {"document": (os.path.basename(file_path), fp)}
            data = {"chat_id": chat_id, "caption": caption}
            resp = requests.post(url, data=data, files=files, timeout=120)
        if resp.status_code == 200:
            logging.info("✅ results.txt sent to Telegram successfully.")
            return True
        else:
            logging.error(f"❌ Telegram send failed: {resp.status_code} {resp.text}")
            return False
    except Exception as e:
        logging.error(f"❌ Telegram send exception: {e}")
        return False

# ---------- input parsing ----------
def parse_pages_input(s):
    s = (s or "1").strip()
    if "-" in s:
        a, b = s.split("-", 1)
        return list(range(int(a), int(b) + 1))
    else:
        return [int(s)]

if __name__ == "__main__":
    pages_env = os.environ.get("PAGES", "1")
    pages = parse_pages_input(pages_env)
    logging.info(f"Starting scraper for pages: {pages}")
    run_scraper(pages)

    # send to telegram if requested
    bot = os.environ.get("TELEGRAM_BOT_TOKEN")
    chat = os.environ.get("TELEGRAM_CHAT_ID")
    if bot and chat:
        logging.info("Sending results.txt to Telegram...")
        send_results_to_telegram(bot, chat, RESULT_FILE)
    else:
        logging.info("TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID not set; skipping Telegram send.")
    logging.info("Done.")
