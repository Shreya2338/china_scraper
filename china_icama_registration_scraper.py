"""
ICAMA China Pesticide Registration Scraper
==========================================
Submits the search form at icama.cn and paginates through ALL results,
saving every registration record to:
  - icama_registrations.csv   (open in Excel)
  - icama_registrations.db    (SQLite, same format as team pipeline)

Fields collected per record:
  RegistrationNo | ProductName | TotalContent | FirstProve |
  Period | Toxicity | Company | Remark

How to run:
  python china_icama_registration_scraper.py

Optional flags:
  python china_icama_registration_scraper.py --pages 50    # stop after 50 pages
  python china_icama_registration_scraper.py --delay 2     # 2s between requests
  python china_icama_registration_scraper.py --include-void # include expired certs

Install dependencies first:
  pip install requests beautifulsoup4 lxml
"""

import csv
import sqlite3
import time
import random
import argparse
import logging
import sys
from pathlib import Path
from datetime import datetime
from typing import List, Dict, Optional

import requests
from bs4 import BeautifulSoup

# ─────────────────────────────────────────────────────────────────────────────
# Config
# ─────────────────────────────────────────────────────────────────────────────

BASE_URL   = "https://www.icama.cn"
SEARCH_URL = f"{BASE_URL}/BasicdataSystem/pesticideRegistrationEn/queryselect_en.do"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Referer":      SEARCH_URL,
    "Content-Type": "application/x-www-form-urlencoded",
    "Origin":       BASE_URL,
}

CSV_FILE = Path("icama_registrations.csv")
DB_FILE  = Path("icama_registrations.db")
LOG_FILE = Path("icama_registration_scraper.log")

COLUMNS = [
    "RegistrationNo",
    "ProductName",
    "TotalContent",
    "FirstProve",
    "Period",
    "Toxicity",
    "Company",
    "Remark",
    "ScrapedAt",
]


# ─────────────────────────────────────────────────────────────────────────────
# Logging
# ─────────────────────────────────────────────────────────────────────────────

def setup_logging() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        handlers=[
            logging.FileHandler(LOG_FILE, encoding="utf-8"),
            logging.StreamHandler(sys.stdout),
        ],
    )


# ─────────────────────────────────────────────────────────────────────────────
# Database
# ─────────────────────────────────────────────────────────────────────────────

def init_db(db_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path)
    conn.execute(f"""
        CREATE TABLE IF NOT EXISTS registrations (
            {', '.join(f'{c} TEXT' for c in COLUMNS)},
            PRIMARY KEY (RegistrationNo)
        )
    """)
    conn.commit()
    logging.info("Database ready at %s", db_path)
    return conn


def save_to_db(conn: sqlite3.Connection, rows: List[Dict]) -> int:
    if not rows:
        return 0
    placeholders = ", ".join("?" * len(COLUMNS))
    sql = f"INSERT OR REPLACE INTO registrations VALUES ({placeholders})"
    data = [tuple(r.get(c, "") for c in COLUMNS) for r in rows]
    conn.executemany(sql, data)
    conn.commit()
    return len(data)


# ─────────────────────────────────────────────────────────────────────────────
# CSV
# ─────────────────────────────────────────────────────────────────────────────

def init_csv(csv_path: Path) -> None:
    if not csv_path.exists():
        with open(csv_path, "w", newline="", encoding="utf-8-sig") as f:
            writer = csv.DictWriter(f, fieldnames=COLUMNS)
            writer.writeheader()
        logging.info("CSV ready at %s", csv_path)


def append_to_csv(csv_path: Path, rows: List[Dict]) -> None:
    if not rows:
        return
    with open(csv_path, "a", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=COLUMNS)
        for row in rows:
            writer.writerow({c: row.get(c, "") for c in COLUMNS})


# ─────────────────────────────────────────────────────────────────────────────
# Parsing
# ─────────────────────────────────────────────────────────────────────────────

def parse_page(html: str) -> List[Dict]:
    """Extract all registration rows from a results page."""
    soup = BeautifulSoup(html, "lxml")
    rows: List[Dict] = []
    now = datetime.utcnow().isoformat()

    # The results table has id="tab"
    table = soup.find("table", {"id": "tab"})
    if not table:
        # fallback: any table with RegistrationNo header
        for tbl in soup.find_all("table"):
            if "RegistrationNo" in tbl.get_text():
                table = tbl
                break

    if not table:
        logging.debug("No results table found on page")
        return rows

    trs = table.find_all("tr")
    for tr in trs:
        tds = tr.find_all("td")
        if len(tds) < 7:
            continue  # skip header or empty rows

        # Pull text cleanly
        def cell(i: int) -> str:
            return tds[i].get_text(separator=" ", strip=True) if i < len(tds) else ""

        reg_no = cell(0)
        # Skip header row that contains column names
        if reg_no.lower() in ("registrationno", "registration no", ""):
            continue

        rows.append({
            "RegistrationNo": reg_no,
            "ProductName":    cell(1),
            "TotalContent":   cell(2),
            "FirstProve":     cell(3),
            "Period":         cell(4),
            "Toxicity":       cell(5),
            "Company":        cell(6),
            "Remark":         cell(7) if len(tds) > 7 else "",
            "ScrapedAt":      now,
        })

    return rows


def get_total_pages(html: str) -> Optional[int]:
    """Try to extract total page count from the pagination area."""
    soup = BeautifulSoup(html, "lxml")

    # Look for pagination text like "1/500" or "共500页"
    pagination = soup.find("div", class_="pagination")
    if pagination:
        text = pagination.get_text()
        import re
        # Pattern: number/number  e.g. "1/500"
        m = re.search(r'/\s*(\d+)', text)
        if m:
            return int(m.group(1))
        # Pattern: 共N页
        m = re.search(r'共\s*(\d+)\s*页', text)
        if m:
            return int(m.group(1))

    # Fallback: count how many page links there are
    links = soup.select(".pagination a")
    page_nums = []
    import re
    for a in links:
        m = re.search(r'\d+', a.get_text())
        if m:
            page_nums.append(int(m.group()))
    if page_nums:
        return max(page_nums)

    return None


# ─────────────────────────────────────────────────────────────────────────────
# Main scraper
# ─────────────────────────────────────────────────────────────────────────────

def scrape(max_pages: int, delay: float, include_void: bool, page_size: int = 20) -> None:
    session = requests.Session()
    session.headers.update(HEADERS)
    session.verify = False  # ICAMA uses self-signed cert

    # Suppress the SSL warning that comes with verify=False
    import urllib3
    urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

    conn = init_db(DB_FILE)
    init_csv(CSV_FILE)

    total_records = 0
    page_no = 1
    total_pages = None

    logging.info("Starting ICAMA registration scrape | max_pages=%s page_size=%d", max_pages, page_size)

    while True:
        if max_pages and page_no > max_pages:
            logging.info("Reached max_pages limit (%d)", max_pages)
            break

        # Build POST payload — same fields as the HTML form
        payload = {
            "pageNo":   str(page_no),
            "pageSize": str(page_size),
            "djzh":     "",   # Registered number (blank = all)
            "cjmc":     "",   # Company name (blank = all)
            "yxcf":     "",   # Active ingredient (blank = all)
        }
        if include_void:
            payload["includeAllCertification"] = "1"

        try:
            logging.info("Fetching page %d/%s ...", page_no, total_pages or "?")
            resp = session.post(SEARCH_URL, data=payload, timeout=30)
            resp.raise_for_status()
        except requests.RequestException as e:
            logging.warning("Request failed on page %d: %s — retrying in 5s", page_no, e)
            time.sleep(5)
            try:
                resp = session.post(SEARCH_URL, data=payload, timeout=30)
                resp.raise_for_status()
            except requests.RequestException as e2:
                logging.error("Retry failed on page %d: %s — skipping", page_no, e2)
                break

        # Detect encoding (site may serve GBK)
        resp.encoding = resp.apparent_encoding or "utf-8"
        html = resp.text

        # Try to get total pages on first fetch
        if total_pages is None:
            total_pages = get_total_pages(html)
            if total_pages:
                logging.info("Total pages detected: %d (≈%d records)", total_pages, total_pages * page_size)
            else:
                logging.info("Could not detect total pages — will stop when results run out")

        rows = parse_page(html)

        if not rows:
            logging.info("No rows found on page %d — assuming end of results", page_no)
            break

        save_to_db(conn, rows)
        append_to_csv(CSV_FILE, rows)
        total_records += len(rows)

        logging.info(
            "Page %d | +%d rows | total so far: %d",
            page_no, len(rows), total_records,
        )

        # Stop if we've hit the last page
        if total_pages and page_no >= total_pages:
            logging.info("Reached last page (%d)", total_pages)
            break

        page_no += 1

        # Polite delay
        sleep_time = delay + random.uniform(0, 0.5)
        time.sleep(sleep_time)

    conn.close()
    logging.info("=" * 50)
    logging.info("DONE | Total records saved: %d", total_records)
    logging.info("CSV  → %s", CSV_FILE.resolve())
    logging.info("DB   → %s", DB_FILE.resolve())
    logging.info("=" * 50)


# ─────────────────────────────────────────────────────────────────────────────
# CLI
# ─────────────────────────────────────────────────────────────────────────────

def main() -> None:
    setup_logging()

    parser = argparse.ArgumentParser(description="ICAMA China pesticide registration scraper")
    parser.add_argument(
        "--pages", type=int, default=0,
        help="Max pages to scrape (default: 0 = all pages)"
    )
    parser.add_argument(
        "--delay", type=float, default=1.5,
        help="Seconds to wait between pages (default: 1.5)"
    )
    parser.add_argument(
        "--include-void", action="store_true",
        help="Include expired/void registrations"
    )
    parser.add_argument(
        "--page-size", type=int, default=20,
        help="Records per page (default: 20)"
    )
    args = parser.parse_args()

    scrape(
        max_pages=args.pages,
        delay=args.delay,
        include_void=args.include_void,
        page_size=args.page_size,
    )


if __name__ == "__main__":
    main()