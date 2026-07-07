"""
upload.py  —  Upload Step
--------------------------
Uploads clean CSV to TiDB. Table name is derived from the domain
so each business type gets its own table automatically.

  domain=restaurant, city=Bhopal → table: leads_restaurant
  domain=gym,        city=Delhi  → table: leads_gym

The table is created/migrated automatically if it doesn't exist.
"""

import argparse
import csv
import re
from pathlib import Path

from db import get_connection, quote_identifier


UPLOAD_COLUMNS = [
    "name", "domain", "city",
    "official_url", "has_official_website",
    "lead_score", "lead_category",
    "extracted_phone", "google_search_url",
    "scrape_status", "source_platform",
    "linkedin_url", "facebook_url", "reddit_url",
    "lat", "lon", "email", "amenity",
]

TABLE_SCHEMA = {
    "name":                 "VARCHAR(255) NOT NULL",
    "domain":               "VARCHAR(100) NOT NULL DEFAULT ''",
    "city":                 "VARCHAR(100) NOT NULL DEFAULT ''",
    "official_url":        "VARCHAR(500) NOT NULL DEFAULT ''",
    "has_official_website": "TINYINT(1) NOT NULL DEFAULT 0",
    "lead_score":           "INT NOT NULL DEFAULT 0",
    "lead_category":        "VARCHAR(100) NOT NULL DEFAULT ''",
    "extracted_phone":     "VARCHAR(50) NOT NULL DEFAULT ''",
    "google_search_url":   "VARCHAR(700) NOT NULL DEFAULT ''",
    "scrape_status":       "VARCHAR(255) NOT NULL DEFAULT ''",
    "source_platform":     "VARCHAR(100) NOT NULL DEFAULT ''",
    "linkedin_url":        "VARCHAR(500) NOT NULL DEFAULT ''",
    "facebook_url":        "VARCHAR(500) NOT NULL DEFAULT ''",
    "reddit_url":          "VARCHAR(500) NOT NULL DEFAULT ''",
    "lat":                  "DECIMAL(10, 7) NULL",
    "lon":                  "DECIMAL(10, 7) NULL",
    "email":                "VARCHAR(255) NOT NULL DEFAULT ''",
    "amenity":              "VARCHAR(100) NOT NULL DEFAULT ''",
    "updated_at":           "TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP",
    # Composite PK so the same name can exist in different cities
    "__pk":                 "PRIMARY KEY (name(200), city(50))",
}


def table_name_for_domain(domain: str) -> str:
    """'Restaurant' → 'leads_restaurant', 'Yoga Studio' → 'leads_yoga_studio'"""
    safe = re.sub(r"[^\w]", "_", domain.strip().lower())
    safe = re.sub(r"_+", "_", safe).strip("_")
    return f"leads_{safe}"


def read_rows(path):
    with Path(path).open("r", encoding="utf-8-sig", newline="") as f:
        yield from csv.DictReader(f)


def normalize_bool(raw):
    return 1 if str(raw).strip().lower() in {"1", "true", "yes", "y", "on"} else 0


def normalize_decimal(raw):
    raw = str(raw or "").strip()
    return raw or None


def normalize_int(raw):
    raw = str(raw or "").strip()
    return int(float(raw)) if raw else 0


def clean_value(column, raw):
    if column == "has_official_website":
        return normalize_bool(raw)
    if column == "lead_score":
        return normalize_int(raw)
    if column in {"lat", "lon"}:
        return normalize_decimal(raw)
    return str(raw or "").strip()


def ensure_table(cursor, tbl: str):
    cursor.execute(
        "SELECT COUNT(*) AS count FROM information_schema.TABLES "
        "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = %s",
        (tbl,),
    )
    if cursor.fetchone()["count"] == 0:
        # Build CREATE TABLE — handle the __pk special entry
        col_parts = []
        for col, defn in TABLE_SCHEMA.items():
            if col == "__pk":
                col_parts.append(defn)
            else:
                col_parts.append(f"{quote_identifier(col)} {defn}")
        cursor.execute(
            f"CREATE TABLE {quote_identifier(tbl)} (\n  "
            + ",\n  ".join(col_parts)
            + "\n)"
        )
        print(f"  Created table: {tbl}")
        return

    # Table exists — add any missing columns
    cursor.execute(f"SHOW COLUMNS FROM {quote_identifier(tbl)}")
    existing = {row["Field"] for row in cursor.fetchall()}
    for col, defn in TABLE_SCHEMA.items():
        if col == "__pk":
            continue
        if col not in existing:
            cursor.execute(
                f"ALTER TABLE {quote_identifier(tbl)} "
                f"ADD COLUMN {quote_identifier(col)} {defn}"
            )
            print(f"  Added column: {col} to {tbl}")


def upsert_rows(cursor, rows, tbl: str) -> int:
    if not rows:
        return 0

    col_sql    = ", ".join(quote_identifier(c) for c in UPLOAD_COLUMNS)
    placeholders = ", ".join(["%s"] * len(UPLOAD_COLUMNS))
    updates    = ", ".join(
        f"{quote_identifier(c)} = VALUES({quote_identifier(c)})"
        for c in UPLOAD_COLUMNS
        if c not in ("name", "city")
    )
    sql = (
        f"INSERT INTO {quote_identifier(tbl)} ({col_sql}) "
        f"VALUES ({placeholders}) "
        f"ON DUPLICATE KEY UPDATE {updates}"
    )
    values = [
        tuple(clean_value(c, row.get(c)) for c in UPLOAD_COLUMNS)
        for row in rows
    ]
    cursor.executemany(sql, values)
    return len(values)


def main():
    parser = argparse.ArgumentParser(description="Upload clean leads to TiDB.")
    parser.add_argument("--input",  default="data/clean_businesses.csv")
    parser.add_argument("--domain", default=None,
                        help="Override domain for table name (auto-detected from CSV if omitted)")
    args = parser.parse_args()

    input_path = Path(args.input)
    if not input_path.exists():
        print(f"❌  Input file not found: {input_path}")
        return 1

    rows = list(read_rows(input_path))
    if not rows:
        print("No rows to upload.")
        return 0

    # Determine domain — from arg or first row
    domain = args.domain or rows[0].get("domain", "business")
    tbl    = table_name_for_domain(domain)
    print(f"Uploading to table: {tbl}")

    conn = get_connection()
    try:
        with conn.cursor() as cursor:
            ensure_table(cursor, tbl)
            uploaded = upsert_rows(cursor, rows, tbl)
        conn.commit()
    finally:
        conn.close()

    print(f"✅  Uploaded {uploaded} rows → {tbl}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
