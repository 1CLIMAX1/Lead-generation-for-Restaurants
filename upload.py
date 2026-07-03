import argparse
import csv
from pathlib import Path

from db import get_connection, quote_identifier


TABLE_NAME = "Restaurant_information"
UPLOAD_COLUMNS = [
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


def read_rows(path):
    with Path(path).open("r", encoding="utf-8-sig", newline="") as infile:
        yield from csv.DictReader(infile)


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


TABLE_SCHEMA = {
    "name": "VARCHAR(255) NOT NULL PRIMARY KEY",
    "official_url": "VARCHAR(500) NOT NULL DEFAULT ''",
    "has_official_website": "TINYINT(1) NOT NULL DEFAULT 0",
    "lead_score": "INT NOT NULL DEFAULT 0",
    "lead_category": "VARCHAR(100) NOT NULL DEFAULT ''",
    "extracted_phone": "VARCHAR(50) NOT NULL DEFAULT ''",
    "google_search_url": "VARCHAR(700) NOT NULL DEFAULT ''",
    "scrape_status": "VARCHAR(255) NOT NULL DEFAULT ''",
    "lat": "DECIMAL(10, 7) NULL",
    "lon": "DECIMAL(10, 7) NULL",
    "email": "VARCHAR(255) NOT NULL DEFAULT ''",
    "amenity": "VARCHAR(100) NOT NULL DEFAULT ''",
    "updated_at": "TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP",
}


def ensure_table(cursor, table_name):
    cursor.execute(
        f"SELECT COUNT(*) AS count FROM information_schema.TABLES "
        f"WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = %s",
        (table_name,),
    )
    if cursor.fetchone()["count"] == 0:
        columns_sql = ",\n            ".join(
            f"{quote_identifier(name)} {definition}"
            for name, definition in TABLE_SCHEMA.items()
        )
        cursor.execute(
            f"CREATE TABLE {quote_identifier(table_name)} (\n            {columns_sql}\n        )"
        )
        return

    cursor.execute(f"SHOW COLUMNS FROM {quote_identifier(table_name)}")
    existing_columns = {row["Field"] for row in cursor.fetchall()}
    for column, definition in TABLE_SCHEMA.items():
        if column not in existing_columns:
            cursor.execute(
                f"ALTER TABLE {quote_identifier(table_name)} "
                f"ADD COLUMN {quote_identifier(column)} {definition}"
            )


def upsert_rows(cursor, rows, table_name):
    if not rows:
        return 0

    column_sql = ", ".join(quote_identifier(column) for column in UPLOAD_COLUMNS)
    placeholders = ", ".join(["%s"] * len(UPLOAD_COLUMNS))
    updates = ", ".join(
        f"{quote_identifier(column)} = VALUES({quote_identifier(column)})"
        for column in UPLOAD_COLUMNS
        if column != "name"
    )
    sql = (
        f"INSERT INTO {quote_identifier(table_name)} ({column_sql}) "
        f"VALUES ({placeholders}) "
        f"ON DUPLICATE KEY UPDATE {updates}"
    )

    values = [
        tuple(clean_value(column, row.get(column)) for column in UPLOAD_COLUMNS)
        for row in rows
    ]
    cursor.executemany(sql, values)
    return len(values)


def main():
    parser = argparse.ArgumentParser(description="Upload clean restaurant leads to TiDB.")
    parser.add_argument("--input", default="data/clean_businesses.csv", help="Clean CSV input path.")
    parser.add_argument("--table", default=TABLE_NAME, help="Target table name.")
    args = parser.parse_args()

    input_path = Path(args.input)
    if not input_path.exists():
        print(f"Input file not found: {input_path}")
        return 1

    rows = list(read_rows(input_path))
    conn = get_connection()
    try:
        with conn.cursor() as cursor:
            ensure_table(cursor, args.table)
            uploaded = upsert_rows(cursor, rows, args.table)
        conn.commit()
    finally:
        conn.close()

    print(f"Uploaded {uploaded} rows into {args.table}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
