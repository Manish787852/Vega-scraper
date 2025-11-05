import os, re, json, time, logging, requests
from bs4 import BeautifulSoup
from playwright.sync_api import sync_playwright

# ====================== CONFIG ======================
BASE_URL = "https://vegamoviesog.city"
RESULT_FILE = "results.txt"
SCRAPED_FILE = "scraped.json"

# Priority order for final links
HOST_PRIORITY = ["gdtot", "gdflix", "xcloud", "v-cloud", "gdrive", "filebee", "filepress", "direct"]

# Allowed button text (Batch/Zip, Download, V-Cloud)
ACCEPT_BUTTON_KEYWORDS = ["batch", "zip", "download", "v-cloud", "vcloud", "v cloud"]

# Skip episode-wise content
EPISODE_KEYWORDS = ["episode", "ep.", "ep-", "ep_", "s0", "s1", "season", "e0", "e1"]

# Telegram bot config (Railway Variables)
BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "")

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")


# ====================== UTILITIES ======================
def load_scraped():
    return json.load(open(SCRAPED_FILE, "r", encoding="utf-8")) if os.path.exists(SCRAPED_FILE) else {}

def save_scraped():
    json.dump(scraped, open(SCRAPED_FILE, "w", encoding="utf-8"), indent=2)

def clean_title(text):
    text = re.sub(r"(download|bluray|web[- ]?dl|zip|episode|hindi|english|dual|subs|x264|x265|10bit|hevc|rip|pack|hdrip|brrip)", "", text, flags=re.I)
    text = re.sub(r"\s+", " ", text).strip()
    return text.title()

def extract_quality(text):
    match = re.search(r"(2160p|1080p|720p|480p)", text, re.I)
    return match.group(1) if match else "Unknown"

def is_episode(text):
    return any(e in text.lower() for e in EPISODE_KEYWORDS)

def send_to_telegram(token, chat_id, filepath):
    if not token or not chat_id:
        logging.warning("⚠️ Telegram bot token or chat ID not set.")
        return
    if not os.path.exists(filepath):
        logging.warning("⚠️ results.txt not found.")
        return
    try:
        with open(filepath, "rb") as f:
            resp = requests.post(f"https://api.telegram.org/bot{token}/sendDocument",
                                 data={"chat_id": chat_id}, files={"document": f})
        if resp.status_code == 200:
            logging.info("📤 Results sent to Telegram successfully.")
        else:
            logging.error(f"❌ Telegram upload failed: {resp.text}")
    except Exception as e:
        logging.error(f"❌ Telegram send error: {e}")


# ====================== VGMLINKZ PARSER ======================
def extract_vgmlinks_html(html):
    soup = BeautifulSoup(html, "html.parser")
    found_links = []
    for a in soup.find_all("a", href=True):
        href = a["href"].strip()
        text = a.get_text(strip=True)
        if any(h in href for h in HOST_PRIORITY) and not is_episode(text):
            found_links.append((href, text))
    return found_links


def resolve_vgmlinkz(vgm_url, context):
    """Open a VGMLinkz page, extract real links directly from HTML."""
    vpage = context.new_page()
    try:
        vpage.goto(vgm_url, wait_until="networkidle", timeout=120000)
        time.sleep(3)
        html = vpage.content()
        found = extract_vgmlinks_html(html)

        if not found:
            logging.warning(f"⚠️ No valid links found in {vgm_url}")
            return None

        # Choose top priority link
        for host in HOST_PRIORITY:
            for href, _ in found:
                if host in href:
                    logging.info(f"✅ {host.upper()} found: {href}")
                    return href

        return found[0][0]  # fallback
    except Exception as e:
        logging.error(f"❌ VGMLinkz error: {e}")
        return None
    finally:
        vpage.close()


# ====================== MOVIE PAGE ======================
def process_movie(movie_url, context):
    if movie_url in scraped:
        logging.info(f"⏭️ Skipping already scraped: {movie_url}")
        return

    page = context.new_page()
    try:
        page.goto(movie_url, wait_until="domcontentloaded", timeout=120000)
        time.sleep(3)
        soup = BeautifulSoup(page.content(), "html.parser")

        # Extract title and qualities written near buttons
        raw_title = soup.title.get_text() if soup.title else movie_url
        title = clean_title(raw_title)
        logging.info(f"🎬 Processing: {title}")

        vgmlinks = []
        for a in soup.find_all("a", href=True):
            href = a["href"]
            text = a.get_text(" ", strip=True).lower()
            if ("vgml" in href or "vgmlinkz" in href) and any(k in text for k in ACCEPT_BUTTON_KEYWORDS) and not is_episode(text):
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
                quality = extract_quality(vgm)
                with open(RESULT_FILE, "a", encoding="utf-8") as f:
                    f.write(f"{title}  {quality}  {final_link}\n")
                logging.info(f"✅ Saved: {title}  {quality}  {final_link}")

    except Exception as e:
        logging.error(f"❌ Movie failed {movie_url}: {e}")
    finally:
        scraped[movie_url] = True
        save_scraped()
        page.close()


# ====================== SCRAPER RUNNER ======================
def run_scraper(pages):
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True, args=["--no-sandbox"])
        context = browser.new_context()

        # Adblock patterns
        for pat in ["adbull", "mdrive", "boost", "rekonise", "shorteners", "ouo.io"]:
            context.route(f"**/*{pat}*", lambda route: route.abort())

        for page_num in pages:
            page_url = f"{BASE_URL}/page/{page_num}/"
            logging.info(f"🌍 Scraping Page {page_num}: {page_url}")
            page = context.new_page()
            page.goto(page_url, wait_until="domcontentloaded", timeout=120000)
            time.sleep(3)
            soup = BeautifulSoup(page.content(), "html.parser")
            page.close()

            # Get only post links (no collections or guides)
            movie_links = [a["href"] for a in soup.select("h3.entry-title a[href]")]
            logging.info(f"🎞️ Found {len(movie_links)} posts on Page {page_num}")

            for mlink in movie_links:
                process_movie(mlink, context)

        browser.close()


# ====================== MAIN ======================
def parse_pages(pstr):
    if "-" in pstr:
        a, b = map(int, pstr.split("-"))
        return list(range(a, b + 1))
    return [int(pstr)]

if __name__ == "__main__":
    scraped = load_scraped()
    pages_input = os.environ.get("PAGES", "1-2")
    pages = parse_pages(pages_input)
    logging.info(f"🚀 Running scraper for pages: {pages}")
    run_scraper(pages)
    logging.info("✅ Done. Sending results.txt to Telegram...")
    send_to_telegram(BOT_TOKEN, CHAT_ID, RESULT_FILE)
