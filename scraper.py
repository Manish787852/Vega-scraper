#!/usr/bin/env python3
"""
Final Vegamovies scraper (Playwright) — Brave-style script blocking + shortener resolution.

Env vars:
  PAGES                 e.g. "1" or "14-20" (default "1")
  BASE_DOMAIN           e.g. "https://vegamoviesog.city" (default used)
  TELEGRAM_BOT_TOKEN
  TELEGRAM_CHAT_ID

Outputs:
  results.txt   (Title  Quality  Link)   -- two spaces between fields
  scraped.json  (movie_url -> true)
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
RESULT_FILE = os.environ.get("RESULT_FILE", "results.txt")
SCRAPED_FILE = os.environ.get("SCRAPED_FILE", "scraped.json")

# Host priority (prefer earlier)
HOST_PRIORITY = ["gdtot", "gdflix", "hubcloud", "v-cloud", "drive.google", "gdrive", "gdlink"]

# Anchor/button keywords to accept on movie pages
ACCEPT_BUTTON_KEYWORDS = ["batch", "zip", "download", "v-cloud", "vcloud", "download now", "g-direct"]

# Patterns that indicate episode-ish links (skip)
EPISODE_PATTERNS = [r"\bep\b", r"\bep\.", r"\bepisode\b", r"\bs\d{1,2}e\d{1,2}\b", r"\bS\d{1,2}E\d{1,2}\b"]

# Shortener host patterns (we'll resolve these)
SHORTENER_HOSTS = ["vdrive.lol", "m.vdrive.lol", "vdshort", "short", "ouo.io", "shrtco", "shrinkme", "rebrand", "boost.ink", "adf.ly", "ouo", "cutt.ly"]

# Ad / known script domains to block (Brave-style)
BLOCK_PATTERNS_SUBSTR = [
    "doubleclick.net", "googlesyndication", "google-analytics", "adservice.google", "adsby", "adsystem",
    "trk.", "tracking", "adserver", "adclick", "shortener", "short", "mdrive", "m.vdrive", "vdshort", "boost.ink",
    "adbull", "rekonise", "ouo.io", "shrtco", "shrinkme", "adf.ly", "cpro", "cpms", "exo.prefix"
]

# Retries and timeouts
PAGE_LOAD_RETRIES = 3
MOVIE_LOAD_RETRIES = 3
VGMLINK_LOAD_RETRIES = 3
FINAL_LOAD_RETRIES = 2
WAIT_ANCHORS_SECONDS = 8
HEADLESS = True  # True for Railway

# Logging
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")


# ---------------- helpers ----------------
def safe_load_json(path):
    try:
        if os.path.exists(path):
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
    except Exception:
        logging.exception("Failed to load JSON, starting fresh.")
    return {}

scraped = safe_load_json(SCRAPED_FILE)

def save_scraped():
    try:
        with open(SCRAPED_FILE, "w", encoding="utf-8") as f:
            json.dump(scraped, f, indent=2, ensure_ascii=False)
    except Exception:
        logging.exception("Failed saving scraped.json")

def write_result(title, quality, link):
    line = f"{title}  {quality}  {link}"
    with open(RESULT_FILE, "a", encoding="utf-8") as f:
        f.write(line + "\n")
    logging.info(f"✅ Saved: {line}")

def is_episode(text):
    if not text:
        return False
    for p in EPISODE_PATTERNS:
        if re.search(p, text, re.I):
            return True
    return False

def extract_quality(text):
    if not text:
        return "Unknown"
    m = re.search(r"(2160p|1080p|720p|480p|360p)", text, re.I)
    return m.group(1).lower() if m else "Unknown"

def clean_title(raw):
    if not raw:
        return "Unknown"
    s = raw.strip()
    s = re.sub(r"\.(mkv|mp4|avi|mov|zip)$", "", s, flags=re.I)
    s = re.sub(r"\[.*?\]|\{.*?\}", "", s)
    # remove bracketed stuff except years
    def _keep_year(m):
        inner = m.group(1)
        if re.match(r"^(19|20)\d{2}$", inner.strip()):
            return f"({inner})"
        return ""
    s = re.sub(r"\((.*?)\)", _keep_year, s)
    s = re.sub(r"[._\-]+", " ", s)
    junk = [r"\bdownload\b", r"\bweb[- ]?dl\b", r"\bbluray\b", r"\bbrrip\b", r"\bhdrip\b",
            r"\bdual audio\b", r"\bhindi\b", r"\benglish\b", r"\besub\b", r"\bsubs\b",
            r"\bx264\b", r"\bx265\b", r"\b10bit\b", r"\bweb\b", r"\bvegamovies\b"]
    for pat in junk:
        s = re.sub(pat, "", s, flags=re.I)
    s = re.sub(r"\s+", " ", s).strip()
    # Simple title case - keep short words uppercase (like "HD")? we keep it simple
    return s.title()

def prefer_links(links):
    """Sort links by HOST_PRIORITY (links earlier in list are preferred)."""
    def score(h):
        low = h.lower()
        for idx, host in enumerate(HOST_PRIORITY):
            if host in low:
                return idx
        return len(HOST_PRIORITY)
    return sorted(links, key=lambda x: score(x))

def fetch_text_requests(url, timeout=15):
    try:
        r = requests.get(url, timeout=timeout, headers={"User-Agent":"Mozilla/5.0"})
        if r.status_code == 200:
            return r.text
    except Exception:
        pass
    return None

# ---------------- resolution helpers ----------------
def resolve_shortener_and_get_final(context, url, wait_seconds=6):
    """
    If url looks like a shortener, open it with Playwright and wait for redirect.
    Return the final URL (page.url) or the original url on failure.
    """
    low = url.lower()
    if not any(s in low for s in SHORTENER_HOSTS):
        return url
    logging.info(f"🔄 Resolving shortener: {url}")
    try:
        p = context.new_page()
        p.goto(url, wait_until="domcontentloaded", timeout=30000)
        # wait some seconds for JS redirect to happen
        for _ in range(wait_seconds):
            time.sleep(1)
            # if url changed, break
            if p.url and p.url != url:
                break
        final = p.url or url
        p.close()
        logging.info(f"➡️ Resolved to: {final}")
        return final
    except Exception as e:
        logging.warning(f"⚠️ Shortener resolve failed: {e}")
        try:
            p.close()
        except:
            pass
        return url

# ---------------- extraction from VGMLINKS HTML ----------------
def extract_final_candidates_from_html(html):
    """
    Parse VGMLinkz HTML and return list of hrefs that likely point to final hosts.
    """
    soup = BeautifulSoup(html, "html.parser")
    found = []
    for a in soup.find_all("a", href=True):
        href = a["href"].strip()
        if not href.startswith("http"):
            continue
        text = (a.get_text(" ", strip=True) or "").lower()
        if is_episode(text) or is_episode(href):
            continue
        # Accept if host priority string in href OR anchor/button text contains an accepted keyword
        href_low = href.lower()
        if any(h in href_low for h in HOST_PRIORITY) or any(k in text for k in ACCEPT_BUTTON_KEYWORDS):
            found.append(href)
            continue
        # Also accept if link wraps a <button> element (common in your HTML)
        if a.find("button") is not None:
            found.append(href)
    # dedupe preserve order
    dedup = []
    seen = set()
    for x in found:
        if x not in seen:
            dedup.append(x); seen.add(x)
    return dedup

# ---------------- main scraping logic ----------------
def run_scraper(page_list):
    # ensure results exist
    open(RESULT_FILE, "a", encoding="utf-8").close()

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=HEADLESS, args=["--no-sandbox", "--disable-dev-shm-usage"])
        context = browser.new_context(user_agent="Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120 Safari/537.36")
        # Brave-style blocking: abort requests whose URL contain suspicious patterns (ads/shorteners)
        def route_handler(route):
            req = route.request
            url = req.url.lower()
            typ = req.resource_type
            # Block known ad/shortener substrings for any resource type (helps keep DOM clean)
            if any(pat in url for pat in BLOCK_PATTERNS_SUBSTR):
                try:
                    return route.abort()
                except:
                    pass
            # Allow everything else
            try:
                return route.continue_()
            except:
                try:
                    route.abort()
                except:
                    pass

        context.route("**/*", lambda route: route_handler(route))

        page = context.new_page()

        for page_num in page_list:
            list_url = f"{BASE_DOMAIN}/page/{page_num}/"
            logging.info(f"\n📄 Scraping listing page: {list_url}")

            list_html = None
            for attempt in range(1, PAGE_LOAD_RETRIES + 1):
                try:
                    page.goto(list_url, wait_until="domcontentloaded", timeout=45000)
                    time.sleep(1.0)
                    list_html = page.content()
                    break
                except PWTimeout:
                    logging.warning(f"⚠️ Timeout loading list page (attempt {attempt}): {list_url}")
                except Exception as e:
                    logging.warning(f"⚠️ Error loading list page (attempt {attempt}): {e}")
            if not list_html:
                logging.info("Trying requests fallback for list page...")
                list_html = fetch_text_requests(list_url)
                if not list_html:
                    logging.error(f"❌ Could not fetch list page: {list_url}")
                    continue

            soup = BeautifulSoup(list_html, "html.parser")
            anchors = soup.select("h3.entry-title a[href]")
            movie_links = []
            for a in anchors:
                href = a.get("href")
                if href and href.startswith("http"):
                    movie_links.append(href)
            logging.info(f"🔗 Found {len(movie_links)} movie posts on page {page_num}")

            for movie_url in movie_links:
                if scraped.get(movie_url):
                    logging.info(f"⏭️ Skipping: {movie_url}")
                    continue
                process_movie(movie_url, context)

        try:
            page.close()
        except:
            pass
        try:
            browser.close()
        except:
            pass

    logging.info("✅ Scraping done.")


def process_movie(movie_url, context):
    logging.info(f"\n🎬 Processing movie: {movie_url}")
    page = context.new_page()
    movie_html = None
    for attempt in range(1, MOVIE_LOAD_RETRIES + 1):
        try:
            page.goto(movie_url, wait_until="domcontentloaded", timeout=45000)
            time.sleep(1.0)
            movie_html = page.content()
            break
        except PWTimeout:
            logging.warning(f"⚠️ Timeout opening movie page (attempt {attempt}): {movie_url}")
        except Exception as e:
            logging.warning(f"⚠️ Error opening movie page (attempt {attempt}): {e}")

    if not movie_html:
        movie_html = fetch_text_requests(movie_url)
        if not movie_html:
            logging.error(f"❌ Cannot fetch movie page; marking scraped: {movie_url}")
            scraped[movie_url] = True
            save_scraped()
            try: page.close()
            except: pass
            return

    soup = BeautifulSoup(movie_html, "html.parser")
    raw_page_title = (soup.title.string or "") if soup.title else ""
    # find anchors that are VGMLINKS or that contain acceptable button text
    candidates = []
    for a in soup.find_all("a", href=True):
        href = a["href"].strip()
        text = (a.get_text(" ", strip=True) or "").lower()
        # avoid non-http hrefs
        if not href.startswith("http"):
            continue
        # skip episodes by text or href
        if is_episode(text) or is_episode(href):
            continue
        # If anchor points to vgmlink domain or contains vgml in href, and text has keywords => accept
        if ("vgml" in href.lower() or "vgmlink" in href.lower()) and any(k in text for k in ACCEPT_BUTTON_KEYWORDS):
            candidates.append(href)
            continue
        # Or if the anchor text itself includes accept keywords (and not an episode)
        if any(k in text for k in ACCEPT_BUTTON_KEYWORDS):
            candidates.append(href)

    # dedupe and preserve order
    candidate_vgms = []
    seen = set()
    for h in candidates:
        if h not in seen:
            seen.add(h)
            candidate_vgms.append(h)

    if not candidate_vgms:
        logging.warning(f"⚠️ No VGMLINK/BATCH/ZIP/Download found on movie page: {movie_url}")
        scraped[movie_url] = True
        save_scraped()
        try: page.close()
        except: pass
        return

    saved_title_quality = set()  # keep per-movie title+quality duplicates out

    for vgm in candidate_vgms:
        logging.info(f"➡️ Visiting VGMLINK/DIRECT: {vgm}")
        vpage = context.new_page()
        vhtml = None
        for attempt in range(1, VGMLINK_LOAD_RETRIES + 1):
            try:
                vpage.goto(vgm, wait_until="domcontentloaded", timeout=45000)
                time.sleep(1.0)
                # wait a bit for anchors to be created after blocking scripts
                for _ in range(WAIT_ANCHORS_SECONDS):
                    anchors_count = len(vpage.query_selector_all("a[href]"))
                    if anchors_count >= 3:
                        break
                    time.sleep(0.5)
                vhtml = vpage.content()
                break
            except PWTimeout:
                logging.warning(f"⚠️ Timeout loading VGMLINK (attempt {attempt}): {vgm}")
            except Exception as e:
                logging.warning(f"⚠️ Error loading VGMLINK (attempt {attempt}): {e}")

        if not vhtml:
            vhtml = fetch_text_requests(vgm)
            if not vhtml:
                logging.warning(f"⚠️ Could not fetch VGMLINK page: {vgm}")
                try: vpage.close()
                except: pass
                continue

        finals = extract_final_candidates_from_html(vhtml)
        if not finals:
            logging.warning(f"⚠️ No final host anchors found on VGMLINK: {vgm}")
            try: vpage.close()
            except: pass
            continue

        finals = prefer_links(finals)

        # For each final candidate link, resolve shorteners if needed and extract title/quality, then save
        for final in finals:
            # resolve shorteners if seen
            resolved = resolve_shortener_and_get_final(context, final)
            # try to get title/quality from final page's <h5> or title; fallback to movie page title
            raw_title = None
            quality = "Unknown"
            for attempt in range(1, FINAL_LOAD_RETRIES + 1):
                try:
                    fpage = context.new_page()
                    fpage.goto(resolved, wait_until="domcontentloaded", timeout=30000)
                    time.sleep(0.8)
                    fhtml = fpage.content()
                    fsoup = BeautifulSoup(fhtml, "html.parser")
                    h5 = fsoup.find("h5", class_=re.compile(r"m-0|font-weight-bold", re.I))
                    if h5 and h5.get_text(strip=True):
                        raw_title = h5.get_text(" ", strip=True)
                    else:
                        # fallback to page title
                        try:
                            ttitle = fpage.title()
                        except:
                            ttitle = None
                        if ttitle:
                            raw_title = ttitle
                    fpage.close()
                    if raw_title:
                        break
                except Exception:
                    try:
                        fpage.close()
                    except:
                        pass
                    # try requests fallback
                    txt = fetch_text_requests(resolved)
                    if txt:
                        fsoup = BeautifulSoup(txt, "html.parser")
                        h5 = fsoup.find("h5", class_=re.compile(r"m-0|font-weight-bold", re.I))
                        if h5 and h5.get_text(strip=True):
                            raw_title = h5.get_text(" ", strip=True)
                            break
            if not raw_title:
                raw_title = raw_page_title or movie_url.split("/")[-2].replace("-", " ")

            quality = extract_quality(raw_title)
            title_clean = clean_title(raw_title)
            key = f"{title_clean}||{quality}"
            if key in saved_title_quality:
                logging.info(f"⏭️ Already saved: {title_clean} {quality}")
                continue

            write_result(title_clean, quality, resolved)
            saved_title_quality.add(key)

        try:
            vpage.close()
        except:
            pass

    scraped[movie_url] = True
    save_scraped()
    try:
        page.close()
    except:
        pass

# ---------------- telegram upload ----------------
def send_results_to_telegram(bot_token, chat_id, file_path):
    if not bot_token or not chat_id:
        logging.warning("Telegram token/chat not provided; skipping send.")
        return False
    if not os.path.exists(file_path):
        logging.warning("Results file not found; skipping send.")
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

# ---------------- CLI / env parsing ----------------
def parse_pages_input(s):
    s = (s or "1").strip()
    if "-" in s:
        a, b = s.split("-", 1)
        return list(range(int(a), int(b) + 1))
    return [int(s)]

if __name__ == "__main__":
    PAGES = os.environ.get("PAGES", "1")
    pages = parse_pages_input(PAGES)
    logging.info(f"Starting scraper for pages: {pages}")
    run_scraper(pages)

    # send to telegram if configured
    BOT = os.environ.get("TELEGRAM_BOT_TOKEN")
    CHAT = os.environ.get("TELEGRAM_CHAT_ID")
    if BOT and CHAT:
        logging.info("Sending results.txt to Telegram...")
        send_results_to_telegram(BOT, CHAT, RESULT_FILE)
    else:
        logging.info("Telegram not configured; skipping send.")
    logging.info("All done.")
