"""
scraper.py  —  Universal Lead Scraper
--------------------------------------
Searches for businesses by domain + location across multiple platforms
using the Serper API. No input CSV needed for on-demand scraping.

CLI:
    python scraper.py --domain "restaurant" --location "Bhopal"
    python scraper.py --domain "gym" --location "Delhi" --sources google linkedin facebook
    python scraper.py --domain "salon" --location "Pune" --count 30

Sources supported (all via Serper — no direct platform scraping):
    google    →  standard Google Search
    linkedin  →  site:linkedin.com/company queries
    facebook  →  site:facebook.com queries
    reddit    →  site:reddit.com queries
    justdial  →  site:justdial.com queries

Output → data/raw_{domain}_{location}.csv
"""

import argparse
import csv
import os
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse, quote_plus

import requests

# ── Config ────────────────────────────────────────────────────────────────────
SERPER_API_KEY = ""           # or set env var SERPER_API_KEY
DELAY_SECONDS  = 1.2          # delay between API calls

# ── RAW columns written to CSV ────────────────────────────────────────────────
RAW_COLUMNS = [
    "name", "domain", "city",
    "website", "phone", "email", "amenity", "lat", "lon",
    "source_platform", "source", "scraped_at",
    "zomato", "swiggy", "magicpin", "dineout", "eazydiner",
    "justdial", "tripadvisor", "foodpanda",
    "linkedin_url", "facebook_url", "reddit_url",
    "total_referral_links", "google_search_url",
]

# ── Platform query templates ──────────────────────────────────────────────────
# Each source generates different Serper queries to find businesses
PLATFORM_QUERIES = {
    "google":   [
        "{domain} {location}",
        "best {domain} in {location}",
        "top {domain} {location}",
    ],
    "linkedin": [
        'site:linkedin.com/company "{domain}" "{location}"',
        'site:linkedin.com/company {domain} {location}',
    ],
    "facebook": [
        'site:facebook.com "{domain}" "{location}"',
        'site:facebook.com/pages {domain} {location}',
    ],
    "reddit": [
        'site:reddit.com "{domain}" "{location}" recommend',
        'site:reddit.com {domain} {location}',
    ],
    "justdial": [
        'site:justdial.com {domain} {location}',
    ],
}

# ── Master domain blocklist ───────────────────────────────────────────────────
_TWO_PART_TLDS = {"co.in", "co.uk", "com.au", "co.nz", "co.za", "net.in"}

DOMAIN_MAP = {
    # Food delivery
    "zomato.com":            "zomato",
    "swiggy.com":            "swiggy",
    "foodpanda.in":          "foodpanda",
    "dunzo.com":             "_reject",
    # Discovery / reviews
    "magicpin.in":           "magicpin",
    "dineout.co.in":         "dineout",
    "eazydiner.com":         "eazydiner",
    "tripadvisor.com":       "tripadvisor",
    "tripadvisor.in":        "tripadvisor",
    "tripadvisor.co.uk":     "tripadvisor",
    "yelp.com":              "_reject",
    "yelp.in":               "_reject",
    # Indian directories
    "justdial.com":          "justdial",
    "sulekha.com":           "_reject",
    "indiamart.com":         "_reject",
    "tradeindia.com":        "_reject",
    "yellowpages.in":        "_reject",
    "burrp.com":             "_reject",
    "nearmetrade.com":       "_reject",
    "asklaila.com":          "_reject",
    "grotal.com":            "_reject",
    "clickindia.com":        "_reject",
    "idbf.in":               "_reject",
    "district.in":           "_reject",
    # Maps / navigation
    "google.com":            "_reject",
    "google.co.in":          "_reject",
    "maps.google.com":       "_reject",
    "maps.app.goo.gl":       "_reject",
    "goo.gl":                "_reject",
    "bing.com":              "_reject",
    "mappls.com":            "_reject",
    "mapmyindia.com":        "_reject",
    "latlong.net":           "_reject",
    "openstreetmap.org":     "_reject",
    # Travel / hotels
    "makemytrip.com":        "_reject",
    "agoda.com":             "_reject",
    "booking.com":           "_reject",
    "goibibo.com":           "_reject",
    "yatra.com":             "_reject",
    "cleartrip.com":         "_reject",
    # Social (treated as platform sources, not official sites)
    "facebook.com":          "facebook_url",
    "instagram.com":         "_reject",
    "twitter.com":           "_reject",
    "x.com":                 "_reject",
    "youtube.com":           "_reject",
    "linkedin.com":          "linkedin_url",
    "pinterest.com":         "_reject",
    "snapchat.com":          "_reject",
    "whatsapp.com":          "_reject",
    # Reddit
    "reddit.com":            "reddit_url",
    # News / encyclopedic
    "wikipedia.org":         "_reject",
    "wikimedia.org":         "_reject",
    "timesofindia.com":      "_reject",
    "ndtv.com":              "_reject",
    "hindustantimes.com":    "_reject",
    "thehindu.com":          "_reject",
    "indiatimes.com":        "_reject",
    "business-standard.com": "_reject",
    "livemint.com":          "_reject",
    "economictimes.com":     "_reject",
    "moneycontrol.com":      "_reject",
    "dnaindia.com":          "_reject",
    "firstpost.com":         "_reject",
    "scroll.in":             "_reject",
    "thewire.in":            "_reject",
    "theprint.in":           "_reject",
    # Menu aggregators
    "restaurantji.com":      "_reject",
    "allmenus.com":          "_reject",
    "zmenu.com":             "_reject",
    "menupix.com":           "_reject",
    "menuwithprice.com":     "_reject",
    "menuonline.in":         "_reject",
    "zagerz.com":            "_reject",
    "foodadvisor.in":        "_reject",
    # Booking
    "opentable.com":         "_reject",
    "resdiary.com":          "_reject",
    "tablesready.com":       "_reject",
    # Free site builders
    "wordpress.com":         "_reject",
    "blogspot.com":          "_reject",
    "wixsite.com":           "_reject",
    "weebly.com":            "_reject",
    "squarespace.com":       "_reject",
    "godaddysites.com":      "_reject",
    "simplesite.com":        "_reject",
    "yolasite.com":          "_reject",
    "webnode.com":           "_reject",
    "site123.me":            "_reject",
    # PR / press
    "prnewswire.com":        "_reject",
    "businesswire.com":      "_reject",
    "globenewswire.com":     "_reject",
    # Government
    "mca.gov.in":            "_reject",
    "india.gov.in":          "_reject",
    # Generic travel/food blogs
    "happytrips.com":        "_reject",
    "holidayiq.com":         "_reject",
    "nativeplanet.com":      "_reject",
    "mapsofindia.com":       "_reject",
    "indiamike.com":         "_reject",
}

REFERRAL_COLUMNS = [
    "zomato", "swiggy", "magicpin", "dineout",
    "eazydiner", "justdial", "tripadvisor", "foodpanda",
]

PLATFORM_URL_COLUMNS = ["linkedin_url", "facebook_url", "reddit_url"]


# ── Domain helpers ────────────────────────────────────────────────────────────

def get_base_domain(url: str) -> str:
    try:
        netloc = urlparse(url).netloc.lower().split(":")[0]
        if not netloc:
            return ""
        parts = netloc.split(".")
        if len(parts) >= 3 and ".".join(parts[-2:]) in _TWO_PART_TLDS:
            return ".".join(parts[-3:])
        if len(parts) >= 2:
            return ".".join(parts[-2:])
        return netloc
    except Exception:
        return ""


def lookup_domain(url: str) -> str:
    base = get_base_domain(url)
    if base in DOMAIN_MAP:
        return DOMAIN_MAP[base]
    try:
        full = urlparse(url).netloc.lower().split(":")[0]
        if full in DOMAIN_MAP:
            return DOMAIN_MAP[full]
    except Exception:
        pass
    return ""


def is_official_website(url: str) -> bool:
    if not url:
        return False
    if lookup_domain(url):
        return False
    path = urlparse(url).path.lower()
    for pat in (r"/restaurants?/", r"/places?/", r"/business/",
                r"/listing/", r"/directory/", r"/food/", r"/company/"):
        if re.search(pat, path):
            return False
    return True


# ── Serper API ────────────────────────────────────────────────────────────────

def serper_search(query: str, api_key: str) -> dict:
    resp = requests.post(
        "https://google.serper.dev/search",
        headers={"X-API-KEY": api_key, "Content-Type": "application/json"},
        json={"q": query, "num": 10, "gl": "in", "hl": "en"},
        timeout=15,
    )
    resp.raise_for_status()
    return resp.json()


# ── Phone extraction ──────────────────────────────────────────────────────────

def extract_phone(text: str) -> str:
    cleaned = re.sub(r"[\s\-\(\)]", "", str(text or ""))
    match = re.search(r"(?:\+91|0)?[6-9]\d{9}", cleaned)
    return match.group(0) if match else ""


# ── Name extraction from search results ──────────────────────────────────────

def extract_name_from_result(result: dict, domain: str, location: str) -> str:
    """
    Pull a clean business name from a search result.
    Strips common suffixes like '- Zomato', '| JustDial', location names etc.
    """
    title = result.get("title", "").strip()
    if not title:
        return ""

    # Strip trailing site names
    for suffix in [
        " - Zomato", " | Zomato", " - Swiggy", " | Swiggy",
        " - JustDial", " | JustDial", " - MagicPin", " | MagicPin",
        " - Tripadvisor", " | Tripadvisor", " - Facebook", " | Facebook",
        " - LinkedIn", " | LinkedIn", " - Instagram", " | Instagram",
        " - Google Maps", " | Google Maps", " - IndiaMART", " | IndiaMART",
        " - Sulekha", " | Sulekha", " - EazyDiner", " | EazyDiner",
    ]:
        if title.endswith(suffix):
            title = title[: -len(suffix)].strip()

    # Remove location from name if it appears at the end
    loc_pattern = re.compile(
        r",?\s*" + re.escape(location) + r"\s*$", re.IGNORECASE
    )
    title = loc_pattern.sub("", title).strip()

    # Remove generic suffixes like "- Best X in Y" patterns
    title = re.sub(r"\s*[-|]\s*.{0,40}$", "", title).strip()

    return title if len(title) > 2 else ""


# ── Result parser ─────────────────────────────────────────────────────────────

def parse_results(data: dict, business_domain: str, location: str,
                  source_platform: str, query: str) -> list:
    """
    Parse one Serper response → list of partial lead dicts.
    Each organic result is treated as a potential business.
    """
    leads = []
    scraped_at = datetime.now(timezone.utc).isoformat(timespec="seconds")

    # Knowledge Graph — single authoritative business
    kg = data.get("knowledgeGraph", {})
    if kg.get("title"):
        lead = _empty_lead(business_domain, location, source_platform, scraped_at, query)
        lead["name"] = kg["title"].strip()
        kg_site = kg.get("website", "")
        if is_official_website(kg_site):
            lead["website"] = kg_site
        lead["phone"]  = extract_phone(kg.get("phoneNumber", ""))
        leads.append(lead)

    # Local results — best source for Indian business phones
    for local in data.get("localResults", []):
        lead = _empty_lead(business_domain, location, source_platform, scraped_at, query)
        lead["name"]    = local.get("title", "").strip()
        lead["phone"]   = extract_phone(local.get("phone", ""))
        local_site = local.get("website", "")
        if is_official_website(local_site):
            lead["website"] = local_site
        lead["lat"] = str(local.get("latitude",  ""))
        lead["lon"] = str(local.get("longitude", ""))
        if lead["name"]:
            leads.append(lead)

    # Organic results — one lead per result
    for result in data.get("organic", []):
        url     = result.get("link", "")
        snippet = result.get("snippet", "")
        title   = result.get("title", "")

        name = extract_name_from_result(result, business_domain, location)
        if not name:
            continue

        lead = _empty_lead(business_domain, location, source_platform, scraped_at, query)
        lead["name"] = name

        col = lookup_domain(url)
        if col == "_reject":
            pass
        elif col in REFERRAL_COLUMNS:
            lead[col] = url
        elif col in PLATFORM_URL_COLUMNS:
            lead[col] = url
        elif is_official_website(url):
            lead["website"] = url

        if not lead["phone"]:
            lead["phone"] = extract_phone(snippet)

        leads.append(lead)

    # Dedupe within this batch by name
    seen  = set()
    deduped = []
    for lead in leads:
        key = re.sub(r"\s+", " ", lead["name"].lower().strip())
        if key and key not in seen:
            seen.add(key)
            deduped.append(lead)

    return deduped


def _empty_lead(business_domain, location, source_platform, scraped_at, query):
    lead = {col: "" for col in RAW_COLUMNS}
    lead["domain"]          = business_domain
    lead["city"]            = location
    lead["source_platform"] = source_platform
    lead["source"]          = "serper_api"
    lead["scraped_at"]      = scraped_at
    lead["google_search_url"] = "https://www.google.com/search?q=" + quote_plus(query)
    lead["total_referral_links"] = 0
    return lead


# ── Main ──────────────────────────────────────────────────────────────────────

def run_scrape(business_domain: str, location: str,
               sources: list, count: int, api_key: str,
               output_path: str = None) -> str:
    """
    Core scrape function — callable from CLI or API server.
    Returns the path of the written raw CSV.
    """
    business_domain = business_domain.strip().lower()
    location        = location.strip()

    if output_path is None:
        safe_domain   = re.sub(r"[^\w]", "_", business_domain)
        safe_location = re.sub(r"[^\w]", "_", location.lower())
        output_path   = f"data/raw_{safe_domain}_{safe_location}.csv"

    all_leads = []
    seen_names = set()

    for source in sources:
        if source not in PLATFORM_QUERIES:
            print(f"  ⚠  Unknown source '{source}', skipping")
            continue

        queries = PLATFORM_QUERIES[source]
        print(f"\n[{source.upper()}] Running {len(queries)} queries...")

        for query_tpl in queries:
            if len(all_leads) >= count:
                break

            query = query_tpl.format(domain=business_domain, location=location)
            print(f"  Searching: {query}")

            try:
                data  = serper_search(query, api_key)
                leads = parse_results(data, business_domain, location, source, query)

                new = 0
                for lead in leads:
                    key = re.sub(r"\s+", " ", lead["name"].lower().strip())
                    if key and key not in seen_names:
                        seen_names.add(key)
                        # Count referral links
                        lead["total_referral_links"] = sum(
                            1 for col in REFERRAL_COLUMNS if lead.get(col)
                        )
                        all_leads.append(lead)
                        new += 1

                print(f"    → {new} new leads (total: {len(all_leads)})")

            except requests.HTTPError as e:
                print(f"    ✗ HTTP {e.response.status_code}: {e}")
            except Exception as e:
                print(f"    ✗ Error: {e}")

            time.sleep(DELAY_SECONDS)

    # Write raw CSV
    out = Path(output_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=RAW_COLUMNS, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(all_leads[:count])

    total = min(len(all_leads), count)
    print(f"\n✅  {total} leads → {output_path}")
    return output_path


def main():
    parser = argparse.ArgumentParser(description="Universal lead scraper via Serper API.")
    parser.add_argument("--domain",   required=True,  help='Business type, e.g. "restaurant", "gym", "salon"')
    parser.add_argument("--location", required=True,  help='City, e.g. "Bhopal", "Delhi"')
    parser.add_argument("--sources",  nargs="+",
                        default=["google"],
                        choices=["google", "linkedin", "facebook", "reddit", "justdial"],
                        help="Platforms to search (default: google)")
    parser.add_argument("--count",    type=int, default=50,
                        help="Max leads to collect (default: 50)")
    parser.add_argument("--output",   default=None,
                        help="Output CSV path (auto-generated if not set)")
    args = parser.parse_args()

    api_key = SERPER_API_KEY or os.environ.get("SERPER_API_KEY", "")
    if not api_key:
        print("❌  No API key. Set SERPER_API_KEY at the top of this file or as an env var.")
        return 1

    run_scrape(
        business_domain=args.domain,
        location=args.location,
        sources=args.sources,
        count=args.count,
        api_key=api_key,
        output_path=args.output,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
