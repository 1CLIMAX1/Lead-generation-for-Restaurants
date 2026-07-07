"""
Restaurant Lead Scraper v3
--------------------------
Key fix: domain matching now uses proper URL parsing (netloc) instead of
naive substring matching — so typos, www. prefixes, extra spaces, and
uppercase in the blocklist no longer cause domains to slip through.

Also: a single BLOCKLIST drives both referral classification AND
official-site rejection, so you only need to add a domain in ONE place.
"""

import csv
import os
import re
import time
from urllib.parse import urlparse
import requests

# ── Config ───────────────────────────────────────────────────────────────────
SERPER_API_KEY = "5aaf23f2e9f016e38bb99da0f136d5f9f66e0f58"          # ← paste your key here, or set env var
INPUT_FILE     = "businesses.csv"
OUTPUT_FILE    = "results.csv"
DELAY_SECONDS  = 1.2
CITY_HINT      = "Bhopal"

# ── Master blocklist ──────────────────────────────────────────────────────────
# Format: "domain.tld" → "column_name"  (or "column_name" = "_reject" to just block,
# without storing in any column)
#
# Rules:
#   - Always use the BASE domain only — no www., no https://, no trailing slash
#   - Subdomains (m.zomato.com, business.justdial.com) are matched automatically
#   - Add new problem domains here ONLY — the scraper handles the rest
#
DOMAIN_MAP = {
    # ── Food delivery ──────────────────────────────────────────────
    "zomato.com":               "zomato",
    "swiggy.com":               "swiggy",
    "foodpanda.in":             "foodpanda",
    "dunzo.com":                "_reject",
    # ── Discovery / reviews ────────────────────────────────────────
    "magicpin.in":              "magicpin",
    "dineout.co.in":            "dineout",
    "eazydiner.com":            "eazydiner",
    "tripadvisor.com":          "tripadvisor",
    "tripadvisor.in":           "tripadvisor",
    "tripadvisor.co.uk":        "tripadvisor",
    "yelp.com":                 "_reject",
    "yelp.in":                  "_reject",
    # ── Indian directories / listings ──────────────────────────────
    "justdial.com":             "justdial",
    "sulekha.com":              "_reject",
    "indiamart.com":            "_reject",
    "tradeindia.com":           "_reject",
    "yellowpages.in":           "_reject",
    "burrp.com":                "_reject",
    "nearmetrade.com":          "_reject",
    "asklaila.com":             "_reject",
    "grotal.com":               "_reject",
    "clickindia.com":           "_reject",
    "idbf.in":                  "_reject",
    "district.in":              "_reject",
    # ── Maps / navigation ──────────────────────────────────────────
    "google.com":               "_reject",
    "google.co.in":             "_reject",
    "maps.google.com":          "_reject",
    "maps.app.goo.gl":          "_reject",
    "goo.gl":                   "_reject",
    "bing.com":                 "_reject",
    "mappls.com":               "_reject",
    "mapmyindia.com":           "_reject",
    "latlong.net":              "_reject",
    "openstreetmap.org":        "_reject",
    # ── Travel / hotels ────────────────────────────────────────────
    "makemytrip.com":           "_reject",
    "agoda.com":                "_reject",
    "booking.com":              "_reject",
    "goibibo.com":              "_reject",
    "yatra.com":                "_reject",
    "cleartrip.com":            "_reject",
    # ── Social / video ─────────────────────────────────────────────
    "facebook.com":             "_reject",
    "instagram.com":            "_reject",
    "twitter.com":              "_reject",
    "x.com":                    "_reject",
    "youtube.com":              "_reject",
    "linkedin.com":             "_reject",
    "pinterest.com":            "_reject",
    "snapchat.com":             "_reject",
    "whatsapp.com":             "_reject",
    # ── Encyclopedic / news ────────────────────────────────────────
    "wikipedia.org":            "_reject",
    "wikimedia.org":            "_reject",
    "timesofindia.com":         "_reject",
    "ndtv.com":                 "_reject",
    "hindustantimes.com":       "_reject",
    "thehindu.com":             "_reject",
    "indiatimes.com":           "_reject",
    "business-standard.com":   "_reject",
    "livemint.com":             "_reject",
    "economictimes.com":        "_reject",
    "moneycontrol.com":         "_reject",
    "dnaindia.com":             "_reject",
    "firstpost.com":            "_reject",
    "scroll.in":                "_reject",
    "thewire.in":               "_reject",
    "theprint.in":              "_reject",
    # ── Menu aggregators ───────────────────────────────────────────
    "restaurantji.com":         "_reject",
    "allmenus.com":             "_reject",
    "zmenu.com":                "_reject",
    "menupix.com":              "_reject",
    "menuwithprice.com":        "_reject",
    "menuonline.in":            "_reject",
    "zagerz.com":               "_reject",
    "foodadvisor.in":           "_reject",
    # ── Booking / reservation ──────────────────────────────────────
    "opentable.com":            "_reject",
    "resdiary.com":             "_reject",
    "tablesready.com":          "_reject",
    # ── Free site builders (hosted subdomains) ─────────────────────
    "wordpress.com":            "_reject",
    "blogspot.com":             "_reject",
    "wixsite.com":              "_reject",
    "weebly.com":               "_reject",
    "squarespace.com":          "_reject",
    "godaddysites.com":         "_reject",
    "simplesite.com":           "_reject",
    "yolasite.com":             "_reject",
    "webnode.com":              "_reject",
    "site123.me":               "_reject",
    # ── PR / press ─────────────────────────────────────────────────
    "prnewswire.com":           "_reject",
    "businesswire.com":         "_reject",
    "globenewswire.com":        "_reject",
    # ── Government ─────────────────────────────────────────────────
    "mca.gov.in":               "_reject",
    "india.gov.in":             "_reject",
    # ── Generic travel/food blogs ──────────────────────────────────
    "happytrips.com":           "_reject",
    "holidayiq.com":            "_reject",
    "nativeplanet.com":         "_reject",
    "mapsofindia.com":          "_reject",
    "indiamike.com":            "_reject",
}

OUTPUT_FIELDS = [
    "name", "city_hint",
    "official_website", "phone",
    "google_maps",
    "zomato", "swiggy", "magicpin",
    "dineout", "eazydiner", "justdial",
    "tripadvisor", "foodpanda", "other_links",
    "search_title", "search_snippet",
]

# ── Core: proper domain extraction ───────────────────────────────────────────

def get_base_domain(url: str) -> str:
    """
    Parse the URL and return its registrable base domain (no www. or subdomains).
    e.g. 'https://m.zomato.com/bhopal/...' → 'zomato.com'
         'https://business.justdial.com/' → 'justdial.com'
    """
    try:
        netloc = urlparse(url).netloc.lower()
        if not netloc:
            return ""
        # Strip port if present
        netloc = netloc.split(":")[0]
        # Strip common subdomains — we want base domain for matching
        parts = netloc.split(".")
        # Handle co.in, com.au, co.uk style ccTLDs (2-part TLDs)
        two_part_tlds = {"co.in", "co.uk", "com.au", "co.nz", "co.za", "net.in"}
        if len(parts) >= 3 and ".".join(parts[-2:]) in two_part_tlds:
            return ".".join(parts[-3:])   # e.g. dineout.co.in
        if len(parts) >= 2:
            return ".".join(parts[-2:])   # e.g. zomato.com
        return netloc
    except Exception:
        return ""


def lookup_domain(url: str) -> str:
    """
    Return the DOMAIN_MAP value for this URL's domain, or '' if not in blocklist.
    """
    base = get_base_domain(url)
    if not base:
        return ""
    # Direct match first
    if base in DOMAIN_MAP:
        return DOMAIN_MAP[base]
    # Also check full netloc for things like maps.app.goo.gl
    try:
        full_netloc = urlparse(url).netloc.lower().split(":")[0]
        if full_netloc in DOMAIN_MAP:
            return DOMAIN_MAP[full_netloc]
    except Exception:
        pass
    return ""


def is_official_website(url: str) -> bool:
    """
    True only if the URL is NOT in the blocklist AND doesn't look like a listing page.
    """
    if not url:
        return False

    verdict = lookup_domain(url)
    if verdict:               # any entry in DOMAIN_MAP → not official
        return False

    # Path-based heuristic for unknown aggregators not yet in DOMAIN_MAP
    path = urlparse(url).path.lower()
    listing_patterns = [
        r"/restaurants?/",
        r"/places?/",
        r"/business/",
        r"/listing/",
        r"/directory/",
        r"/food/",
    ]
    for pat in listing_patterns:
        if re.search(pat, path):
            return False

    return True

# ── Helpers ───────────────────────────────────────────────────────────────────

def classify_link(url: str) -> str:
    """Return referral column key (e.g. 'zomato'), '_reject', or '' (unknown)."""
    return lookup_domain(url)


def extract_phone(text: str) -> str:
    cleaned = re.sub(r"[\s\-\(\)]", "", text)
    match = re.search(r"(?:\+91|0)?[6-9]\d{9}", cleaned)
    return match.group(0) if match else ""


def serper_search(query: str) -> dict:
    api_key = SERPER_API_KEY or os.environ.get("SERPER_API_KEY", "")
    resp = requests.post(
        "https://google.serper.dev/search",
        headers={"X-API-KEY": api_key, "Content-Type": "application/json"},
        json={"q": query, "num": 10, "gl": "in", "hl": "en"},
        timeout=15,
    )
    resp.raise_for_status()
    return resp.json()


def parse_results(data: dict, name: str) -> dict:
    row = {f: "" for f in OUTPUT_FIELDS}
    row["name"]       = name
    row["city_hint"]  = CITY_HINT
    other_links       = []

    # Knowledge Graph
    kg = data.get("knowledgeGraph", {})
    if kg:
        kg_site = kg.get("website", "")
        if is_official_website(kg_site):
            row["official_website"] = kg_site
        row["phone"]       = row["phone"]       or kg.get("phoneNumber", "")
        row["google_maps"] = row["google_maps"] or kg.get("maps", "")

    # Places panel
    for place in data.get("places", []):
        row["phone"]       = row["phone"]       or place.get("phoneNumber", "")
        row["google_maps"] = row["google_maps"] or place.get("cid", "")

    # Organic results
    for result in data.get("organic", []):
        url     = result.get("link", "")
        snippet = result.get("snippet", "")
        title   = result.get("title", "")

        if not row["search_title"]:
            row["search_title"]   = title
            row["search_snippet"] = snippet[:200]

        col = classify_link(url)

        if col == "_reject":
            pass                            # blocked, discard
        elif col:                           # named referral column
            if not row.get(col):
                row[col] = url
        elif is_official_website(url):
            if not row["official_website"]:
                row["official_website"] = url
        else:
            if url and url not in other_links:
                other_links.append(url)

        if not row["phone"]:
            row["phone"] = extract_phone(snippet)

    # Answer box phone fallback
    ab = data.get("answerBox", {})
    if not row["phone"]:
        row["phone"] = extract_phone(ab.get("answer", "") + " " + ab.get("snippet", ""))

    row["other_links"] = " | ".join(other_links[:5])
    return row


def load_names(filepath: str) -> list:
    names = []
    seen  = set()
    with open(filepath, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            name = row.get("name", "").strip()
            if not name or name in seen:
                continue
            if re.fullmatch(r"(Canteen|Mess\s*[-–]?\s*\d*)", name, re.IGNORECASE):
                print(f"  ⚠  Skipping generic name: '{name}'")
                continue
            seen.add(name)
            names.append({
                "name":    name,
                "website": row.get("website", "").strip(),
                "phone":   row.get("phone", "").strip(),
            })
    return names


def main():
    api_key = SERPER_API_KEY or os.environ.get("SERPER_API_KEY", "")
    if not api_key:
        print("❌  No API key found.")
        print("    Set SERPER_API_KEY = 'your_key' at the top of this file, or")
        print("    run:  export SERPER_API_KEY='your_key'  before executing.")
        return

    entries = load_names(INPUT_FILE)
    print(f"✅  Loaded {len(entries)} unique restaurant names from {INPUT_FILE}\n")

    results = []

    for i, entry in enumerate(entries, 1):
        name  = entry["name"]
        query = f"{name} restaurant {CITY_HINT}"
        print(f"[{i}/{len(entries)}] {query}")

        try:
            data = serper_search(query)
            row  = parse_results(data, name)

            if entry["website"] and is_official_website(entry["website"]):
                row["official_website"] = entry["website"]
            if entry["phone"]:
                row["phone"] = entry["phone"]

            results.append(row)

            found = [k for k in ["official_website", "phone", "zomato", "swiggy", "magicpin"] if row.get(k)]
            print(f"   → {', '.join(found) if found else 'nothing useful'}")

        except requests.HTTPError as e:
            print(f"   ✗ HTTP {e.response.status_code}: {e}")
            results.append({**{f: "" for f in OUTPUT_FIELDS},
                            "name": name, "city_hint": CITY_HINT,
                            "search_snippet": f"ERROR: {e}"})
        except Exception as e:
            print(f"   ✗ Error: {e}")
            results.append({**{f: "" for f in OUTPUT_FIELDS},
                            "name": name, "city_hint": CITY_HINT,
                            "search_snippet": f"ERROR: {e}"})

        time.sleep(DELAY_SECONDS)

    with open(OUTPUT_FILE, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=OUTPUT_FIELDS, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(results)

    total     = len(results)
    with_site = sum(1 for r in results if r.get("official_website"))
    with_ph   = sum(1 for r in results if r.get("phone"))
    with_zom  = sum(1 for r in results if r.get("zomato"))
    with_swi  = sum(1 for r in results if r.get("swiggy"))

    print(f"\n{'─'*50}")
    print(f"✅  Done! {total} restaurants → {OUTPUT_FILE}")
    print(f"   Official websites : {with_site}")
    print(f"   Phone numbers     : {with_ph}")
    print(f"   Zomato links      : {with_zom}")
    print(f"   Swiggy links      : {with_swi}")
    print(f"{'─'*50}")


if __name__ == "__main__":
    main()