import argparse
import csv
import re
from pathlib import Path
from urllib.parse import urlparse


CLEAN_COLUMNS = [
    "name",
    "official_url",
    "has_official_website",
    "lead_score",
    "lead_category",
    "extracted_phone",
    "google_search_url",
    "scrape_status",
    "lat",
    "lon",
    "email",
    "amenity",
]

REJECTED_COLUMNS = ["reason", *CLEAN_COLUMNS]
PHONE_PATTERN = re.compile(r"[+]?\d[\d\s().-]{6,}\d")
LISTING_DOMAINS = {
    "zomato.com",
    "swiggy.com",
    "justdial.com",
    "tripadvisor.com",
    "restaurant-guru.in",
    "eazydiner.com",
    "magicpin.in",
    "facebook.com",
    "instagram.com",
    "yappe.in",
}


def read_rows(path):
    with Path(path).open("r", encoding="utf-8-sig", newline="") as infile:
        yield from csv.DictReader(infile)


def write_rows(path, fieldnames, rows):
    output_path = Path(path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", encoding="utf-8", newline="") as outfile:
        writer = csv.DictWriter(outfile, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def value(row, *names):
    for name in names:
        raw = row.get(name)
        if raw is not None and str(raw).strip() != "":
            return str(raw).strip()
    return ""


def normalize_bool(raw):
    return str(raw).strip().lower() in {"1", "true", "yes", "y", "on"}


def normalize_phone(raw):
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


def normalize_url(raw):
    url = str(raw or "").strip()
    if not url:
        return ""
    if not url.startswith(("http://", "https://")):
        url = f"https://{url}"
    parsed = urlparse(url)
    if not parsed.netloc or "." not in parsed.netloc:
        return ""
    return url


def domain(url):
    parsed = urlparse(url)
    host = parsed.netloc.lower().removeprefix("www.")
    return host


def is_listing_url(url):
    host = domain(url)
    return any(host == item or host.endswith(f".{item}") for item in LISTING_DOMAINS)


def lead_category(score):
    if score >= 85:
        return "Hot Lead"
    if score >= 70:
        return "High Priority"
    if score >= 40:
        return "Medium Priority"
    return "Strong Online Presence"


def calculate_score(has_official_website, phone, total_referral_links):
    score = 10 if has_official_website else 70
    if not has_official_website and not phone:
        score += 10
    if total_referral_links:
        score += min(total_referral_links * 3, 15)
    return max(0, min(score, 100))


def clean_row(row):
    name = value(row, "name", "restaurant_name", "business_name")
    website = normalize_url(value(row, "official_url", "website", "url"))
    if website and is_listing_url(website):
        website = ""

    existing_has_website = value(row, "has_official_website", "has_website")
    has_official_website = normalize_bool(existing_has_website) if existing_has_website else bool(website)
    if not website:
        has_official_website = False

    phone = normalize_phone(value(row, "extracted_phone", "phone", "Contact No.", "contact_no"))
    total_referral_links = int(float(value(row, "total_referral_links") or 0))
    existing_score = value(row, "lead_score", "score")
    score = int(float(existing_score)) if existing_score else calculate_score(
        has_official_website,
        phone,
        total_referral_links,
    )

    clean = {
        "name": name,
        "official_url": website,
        "has_official_website": "TRUE" if has_official_website else "FALSE",
        "lead_score": str(max(0, min(score, 100))),
        "lead_category": value(row, "lead_category", "category") or lead_category(score),
        "extracted_phone": phone,
        "google_search_url": value(row, "google_search_url", "search_url"),
        "scrape_status": value(row, "scrape_status", "status") or ("found" if phone else "not_found"),
        "lat": value(row, "lat", "latitude"),
        "lon": value(row, "lon", "lng", "longitude"),
        "email": value(row, "email"),
        "amenity": value(row, "amenity"),
    }

    if not name:
        return None, "missing name"
    return clean, ""


def dedupe_key(row):
    return re.sub(r"\s+", " ", row["name"].strip().lower())


def main():
    parser = argparse.ArgumentParser(description="Validate and normalize restaurant lead CSV data.")
    parser.add_argument("--input", default="data/raw_businesses.csv", help="Input CSV path.")
    parser.add_argument("--output", default="data/clean_businesses.csv", help="Clean CSV output path.")
    parser.add_argument("--rejected-output", default="data/rejected_businesses.csv", help="Rejected rows CSV output path.")
    args = parser.parse_args()

    input_path = Path(args.input)
    if not input_path.exists():
        print(f"Input file not found: {input_path}")
        return 1

    clean_rows = []
    rejected_rows = []
    seen = set()

    for source_row in read_rows(input_path):
        clean, reason = clean_row(source_row)
        if clean is None:
            rejected_rows.append({"reason": reason, **{column: "" for column in CLEAN_COLUMNS}})
            continue

        key = dedupe_key(clean)
        if key in seen:
            rejected_rows.append({"reason": "duplicate name", **clean})
            continue

        seen.add(key)
        clean_rows.append(clean)

    write_rows(args.output, CLEAN_COLUMNS, clean_rows)
    write_rows(args.rejected_output, REJECTED_COLUMNS, rejected_rows)
    print(f"Wrote {len(clean_rows)} clean rows to {args.output}")
    print(f"Wrote {len(rejected_rows)} rejected rows to {args.rejected_output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
