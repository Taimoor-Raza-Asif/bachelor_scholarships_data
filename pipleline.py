#!/usr/bin/env python3
"""
EduVision Scholarships Scraper + Cleaner Pipeline
- Scrapes all scholarships
- Extracts structured fields
- Normalizes deadlines
- Filters Bachelor's scholarships
- Saves JSON + CSV outputs
"""

import requests
from bs4 import BeautifulSoup
from urllib.parse import urljoin
import time, csv, json, re, sys
from datetime import datetime, UTC
from requests.adapters import HTTPAdapter, Retry
import pandas as pd
from dateutil import parser

# ---------- CONFIG ----------
BASE = "https://www.eduvision.edu.pk"
LISTING_PATH = "/scholarships/"
USER_AGENT = "eduvision-scraper/1.0 (+your_email@example.com)"
DELAY = 1.2               # seconds between requests
TIMEOUT = 20
OUTPUT_JSON = "eduvision_scholarships.json"
OUTPUT_CSV = "eduvision_scholarships.csv"
OUTPUT_BACHELORS = "bachelors_scholarships.csv"
# ----------------------------

session = requests.Session()
session.headers.update({"User-Agent": USER_AGENT})
retries = Retry(total=5, backoff_factor=0.5, status_forcelist=[500,502,503,504])
session.mount("https://", HTTPAdapter(max_retries=retries))

def fetch(url):
    try:
        r = session.get(url, timeout=TIMEOUT)
        r.raise_for_status()
        return r.text
    except Exception as e:
        print(f"[ERROR] Failed to fetch {url}: {e}", file=sys.stderr)
        return None

def discover_links_from_listing(max_pages=100):
    links = set()
    page = 1
    while page <= max_pages:
        url = urljoin(BASE, f"{LISTING_PATH}?page={page}")
        html = fetch(url)
        if not html:
            break
        soup = BeautifulSoup(html, "html.parser")

        found_links = set()
        for a in soup.select("a[href*='/scholarships/']"):
            href = a.get("href")
            if href and "/scholarships/" in href and not href.endswith("/scholarships/"):
                found_links.add(urljoin(BASE, href).split("#")[0])

        new_links = found_links - links
        print(f"[INFO] Page {page} → {len(new_links)} new links")

        if not new_links:   # stop if no new links discovered
            break

        links.update(new_links)
        page += 1
        time.sleep(DELAY)

    return sorted(links)

def extract_text(el):
    return " ".join(el.stripped_strings) if el else ""

def guess_field_by_heading(soup, heading_texts):
    for h in soup.find_all(re.compile("^h[1-6]$")):
        txt = h.get_text(" ", strip=True).lower()
        for key in heading_texts:
            if key in txt:
                content = []
                for sib in h.next_siblings:
                    if getattr(sib, "name", "") and re.match(r"^h[1-6]$", sib.name):
                        break
                    if getattr(sib, "get_text", None):
                        content.append(extract_text(sib))
                return " ".join(content).strip()
    return ""

def extract_fields(url):
    html = fetch(url)
    if not html:
        return None
    soup = BeautifulSoup(html, "html.parser")

    # Title
    title = extract_text(soup.select_one("h1") or soup.select_one(".post-title") or soup.select_one("title"))

    # Content
    main = soup.select_one("article") or soup.select_one("#content") or soup.body
    full_content = extract_text(main).replace("\xa0", " ")

    text_lower = full_content.lower()

    # Deadline
    deadline = ""
    m = re.search(r"(last date|deadline|apply by)\s*:?([^\n\r]+)", full_content, re.IGNORECASE)
    if m:
        deadline = m.group(2).strip()
    if not deadline:
        deadline = guess_field_by_heading(soup, ["deadline", "last date", "apply by", "closing date"])

    # Eligibility
    eligibility = guess_field_by_heading(soup, ["eligib", "criteria", "requirements", "who can apply"])

    # Amount
    amount = guess_field_by_heading(soup, ["amount", "coverage", "stipend", "funding"])

    # Level
    level = []
    if re.search(r"\bmatric\b", text_lower): level.append("Matric")
    if re.search(r"\binter\b|intermediate", text_lower): level.append("Intermediate")
    if re.search(r"\bbachelor|undergrad", text_lower): level.append("Bachelor")
    if re.search(r"\bms|m\.phil|master", text_lower): level.append("Masters/MPhil")
    if re.search(r"\bphd|doctoral", text_lower): level.append("PhD")
    level = ", ".join(sorted(set(level)))

    # Type (Merit/Need)
    type_ = ""
    if "merit" in text_lower and "need" in text_lower:
        type_ = "Merit & Need Based"
    elif "merit" in text_lower:
        type_ = "Merit Based"
    elif "need" in text_lower:
        type_ = "Need Based"

    # Area
    area = ""
    m_area = re.search(r"Area\s*:?([A-Za-z ,&\-]+)(Deadline|$)", full_content, re.IGNORECASE)
    if m_area:
        area = m_area.group(1).strip()
    if not area:
        area = guess_field_by_heading(soup, ["area", "region", "province"])

    # Apply link
    apply_link = ""
    for a in soup.find_all("a", href=True):
        txt = a.get_text(" ", strip=True).lower()
        if any(k in txt for k in ["apply", "application", "form"]):
            apply_link = urljoin(url, a["href"])
            break

    # Offered by
    offered_by = guess_field_by_heading(soup, ["offered by", "organization", "provider", "sponsored"])

    # Summary
    summary = " ".join(re.split(r"(?<=[.!?])\s+", full_content)[:3]).strip()

    return {
        "title": title,
        "url": url,
        "offered_by": offered_by,
        "level": level,
        "type": type_,
        "amount": amount,
        "eligibility": eligibility,
        "deadline": deadline,
        "area": area,
        "application_link": apply_link,
        "summary": summary,
        "full_content": full_content,
        "scraped_at": datetime.now(UTC).isoformat(),
    }

# ---------- CLEANING PART ----------
def extract_date(text):
    if not isinstance(text, str) or not text.strip():
        return None
    if text.strip().lower().startswith(("n.a", "na", "n/a")):
        return None

    # Numeric date like 15-02-2024
    match = re.search(r"(\d{1,2}[-/]\d{1,2}[-/]\d{2,4}|\d{4}[-/]\d{1,2}[-/]\d{1,2})", text)
    if match:
        try:
            return pd.to_datetime(match.group(0), errors="coerce", dayfirst=True).date()
        except:
            pass

    # Month name formats
    try:
        return parser.parse(text, fuzzy=True, dayfirst=False, ignoretz=True).date()
    except:
        return None

# ---------- MAIN ----------
def main():
    print("[INFO] Discovering scholarship links...")
    links = discover_links_from_listing()
    print(f"[INFO] Found {len(links)} links")

    results = []
    for i, link in enumerate(links, 1):
        print(f"[{i}/{len(links)}] Scraping {link}")
        data = extract_fields(link)
        if data:
            results.append(data)
        time.sleep(DELAY)

    if results:
        # Save raw JSON
        with open(OUTPUT_JSON, "w", encoding="utf-8") as f:
            json.dump(results, f, ensure_ascii=False, indent=2)

        # Save all scholarships CSV
        keys = ["title","url","offered_by","level","type","amount","eligibility",
                "deadline","area","application_link","summary","scraped_at"]

        with open(OUTPUT_CSV, "w", encoding="utf-8", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=keys)
            writer.writeheader()
            for r in results:
                writer.writerow({k: r.get(k, "") for k in keys})

        # Load into DataFrame
        df = pd.DataFrame(results)

        # Clean deadlines
        df["clean_deadline"] = df["deadline"].apply(extract_date)

        # Filter Bachelor only
        bachelors_df = df[df["level"].str.contains("Bachelor", case=False, na=False)].copy()

        # Save Bachelor's CSV
        bachelors_df.to_csv(OUTPUT_BACHELORS, index=False)

        print(f"[INFO] Saved → {OUTPUT_JSON}, {OUTPUT_CSV}, {OUTPUT_BACHELORS}")
    else:
        print("[WARN] No scholarships scraped!")

if __name__ == "__main__":
    main()
