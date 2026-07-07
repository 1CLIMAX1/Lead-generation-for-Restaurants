"""
validator.py  —  Validate & Score Step
---------------------------------------
Reads raw CSV from scraper.py, normalises fields,
calculates lead score, splits into clean / rejected CSVs.

Score logic (higher = better lead — business needs our help):
  No website → base 70    Has website → base 10
  No phone either → +10
  Each referral platform found → +3 (capped at +15)
  LinkedIn/Facebook presence → +5 each (shows online activity but no site)
  Max possible: 100
"""

import argparse
import csv
import re
from pathlib import Path
from urllib.parse import urlparse, quote_plus


CLEAN_COLUMNS = [
    "name", "domain", "city",
    "official_url", "has_official_website",
    "lead_score", "lead_category",
    "extracted_phone", "google_search_url",
    "scrape_status", "source_platform",
    "linkedin_url", "facebook_url", "reddit_url",
    "lat", "lon", "email", "amenity",
]

REJECTED_COLUMNS = ["reason", *CLEAN_COLUMNS]

PHONE_PATTERN = re.compile(r"[+]?\d[\d\s().-]{6,}\d")

REFERRAL_COLUMNS = [
    "zomato", "swiggy", "magicpin", "dineout",
    "eazydiner", "justdial", "tripadvisor", "foodpanda",
]

LISTING_DOMAINS = {
    "zomato.com", "swiggy.com", "justdial.com",
    "tripadvisor.com", "tripadvisor.in",
    "restaurant-guru.in", "eazydiner.com", "magicpin.in",
    "facebook.com", "instagram.com", "yappe.in",
    "sulekha.com", "indiamart.com", "tradeindia.com",
    "yellowpages.in", "burrp.com", "nearmetrade.com",
    "asklaila.com", "grotal.com", "clickindia.com",
    "idbf.in", "district.in", "mappls.com", "mapmyindia.com",
    "latlong.net", "makemytrip.com", "agoda.com",
    "booking.com", "goibibo.com", "yatra.com",
    "foodpanda.in", "dunzo.com", "yelp.com", "yelp.in",
    "restaurantji.com", "allmenus.com", "zmenu.com",
    "menuwithprice.com", "menuonline.in", "zagerz.com",
    "opentable.com", "resdiary.com",
    "wordpress.com", "blogspot.com", "wixsite.com",
    "weebly.com", "squarespace.com", "godaddysites.com",
}


def read_rows(path):
    with Path(path).open("r", encoding="utf-8-sig", newline="") as f:
        yield from csv.DictReader(f)


def write_rows(path, fieldnames, rows):
    out = Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def value(row, *names):
    for name in names:
        raw = row.get(name)
        if raw is not None and str(raw).strip():
            return str(raw).strip()
    return ""


def normalize_phone(raw: str) -> str:
    match = PHONE_PATTERN.search(str(raw or ""))
    if not match:
        return ""
    phone = re.sub(r"[()\s.-]+", "", match.group(0))
    if phone.startswith("+91") and len(phone) == 13:
        return phone
    if phone.startswith("91") and len(phone) == 12:
        return f"+{phone}"
    if len(phone) == 10:
        return f"+91{phone}"
    return phone


def normalize_url(raw: str) -> str:
    url = str(raw or "").strip()
    if not url:
        return ""
    if not url.startswith(("http://", "https://")):
        url = f"https://{url}"
    parsed = urlparse(url)
    if not parsed.netloc or "." not in parsed.netloc:
        return ""
    return url


def is_listing_url(url: str) -> bool:
    host = urlparse(url).netloc.lower().removeprefix("www.")
    return any(host == d or host.endswith(f".{d}") for d in LISTING_DOMAINS)


def calculate_score(has_official_website: bool, phone: str,
                    total_referral_links: int,
                    linkedin_url: str, facebook_url: str) -> int:
    score = 10 if has_official_website else 70

    if not has_official_website:
        if not phone:
            score += 10
        score += min(total_referral_links * 3, 15)
        # Has social presence but no website — still a good lead
        if linkedin_url:
            score += 5
        if facebook_url:
            score += 5

    return max(0, min(score, 100))


def lead_category(score: int) -> str:
    if score >= 85:
        return "Hot Lead"
    if score >= 70:
        return "High Priority"
    if score >= 40:
        return "Medium Priority"
    return "Strong Online Presence"


def clean_row(row: dict):
    name = value(row, "name", "restaurant_name", "business_name")
    if not name:
        return None, "missing name"

    website = normalize_url(value(row, "website", "official_url", "url"))
    if website and is_listing_url(website):
        website = ""

    has_official_website = bool(website)

    phone = normalize_phone(value(row, "phone", "extracted_phone", "Contact No."))

    raw_total = value(row, "total_referral_links")
    if raw_total:
        total_referral_links = int(float(raw_total))
    else:
        total_referral_links = sum(1 for col in REFERRAL_COLUMNS if value(row, col))

    linkedin_url = normalize_url(value(row, "linkedin_url"))
    facebook_url = normalize_url(value(row, "facebook_url"))
    reddit_url   = normalize_url(value(row, "reddit_url"))

    score = calculate_score(
        has_official_website, phone,
        total_referral_links, linkedin_url, facebook_url
    )

    search_url = value(row, "google_search_url", "search_url")
    if not search_url:
        search_url = "https://www.google.com/search?q=" + quote_plus(
            f"{name} {value(row, 'domain', 'amenity')} {value(row, 'city')}"
        )

    domain_val   = value(row, "domain", "amenity") or "business"
    city_val     = value(row, "city")

    clean = {
        "name":                 name,
        "domain":               domain_val,
        "city":                 city_val,
        "official_url":        website,
        "has_official_website": "TRUE" if has_official_website else "FALSE",
        "lead_score":           str(score),
        "lead_category":        value(row, "lead_category") or lead_category(score),
        "extracted_phone":     phone,
        "google_search_url":   search_url,
        "scrape_status":       value(row, "scrape_status") or ("found" if (website or phone) else "not_found"),
        "source_platform":     value(row, "source_platform"),
        "linkedin_url":        linkedin_url,
        "facebook_url":        facebook_url,
        "reddit_url":          reddit_url,
        "lat":                  value(row, "lat", "latitude"),
        "lon":                  value(row, "lon", "lng", "longitude"),
        "email":                value(row, "email"),
        "amenity":              value(row, "amenity"),
    }

    return clean, ""


def dedupe_key(row):
    return (
        re.sub(r"\s+", " ", row["name"].strip().lower()),
        row["city"].strip().lower(),
        row["domain"].strip().lower(),
    )


def main():
    parser = argparse.ArgumentParser(description="Validate and score lead CSV.")
    parser.add_argument("--input",           default="data/raw_businesses.csv")
    parser.add_argument("--output",          default="data/clean_businesses.csv")
    parser.add_argument("--rejected-output", default="data/rejected_businesses.csv")
    args = parser.parse_args()

    input_path = Path(args.input)
    if not input_path.exists():
        print(f"❌  Input file not found: {input_path}")
        return 1

    clean_rows    = []
    rejected_rows = []
    seen          = set()

    for source_row in read_rows(input_path):
        clean, reason = clean_row(source_row)

        if clean is None:
            rejected_rows.append({"reason": reason, **{col: "" for col in CLEAN_COLUMNS}})
            continue

        key = dedupe_key(clean)
        if key in seen:
            rejected_rows.append({"reason": "duplicate name", **clean})
            continue

        seen.add(key)
        clean_rows.append(clean)

    write_rows(args.output,          CLEAN_COLUMNS,    clean_rows)
    write_rows(args.rejected_output, REJECTED_COLUMNS, rejected_rows)

    hot   = sum(1 for r in clean_rows if r["lead_category"] == "Hot Lead")
    high  = sum(1 for r in clean_rows if r["lead_category"] == "High Priority")

    print(f"\n{'─'*50}")
    print(f"✅  {len(clean_rows)} clean → {args.output}")
    print(f"   Hot Leads     : {hot}")
    print(f"   High Priority : {high}")
    print(f"⚠   {len(rejected_rows)} rejected → {args.rejected_output}")
    print(f"{'─'*50}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
