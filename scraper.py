import argparse
import csv
from datetime import datetime, timezone
from pathlib import Path


RAW_COLUMNS = [
    "name",
    "website",
    "phone",
    "email",
    "amenity",
    "lat",
    "lon",
    "source",
    "scraped_at",
]


def read_rows(path):
    with Path(path).open("r", encoding="utf-8", newline="") as infile:
        yield from csv.DictReader(infile)


def write_rows(path, rows):
    output_path = Path(path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", encoding="utf-8", newline="") as outfile:
        writer = csv.DictWriter(outfile, fieldnames=RAW_COLUMNS)
        writer.writeheader()
        writer.writerows(rows)


def normalize_raw_row(row, source, scraped_at):
    return {
        "name": (row.get("name") or row.get("business_name") or "").strip(),
        "website": (row.get("website") or row.get("official_url") or "").strip(),
        "phone": (row.get("phone") or row.get("Contact No.") or row.get("extracted_phone") or "").strip(),
        "email": (row.get("email") or "").strip(),
        "amenity": (row.get("amenity") or "").strip(),
        "lat": (row.get("lat") or row.get("latitude") or "").strip(),
        "lon": (row.get("lon") or row.get("lng") or row.get("longitude") or "").strip(),
        "source": source,
        "scraped_at": scraped_at,
    }


def main():
    parser = argparse.ArgumentParser(description="Create a raw businesses CSV for the lead pipeline.")
    parser.add_argument("--source", default="businesses.csv", help="Existing source CSV to convert into raw format.")
    parser.add_argument("--output", default="data/raw_businesses.csv", help="Raw pipeline CSV output path.")
    args = parser.parse_args()

    source_path = Path(args.source)
    if not source_path.exists():
        print(f"Source file not found: {source_path}")
        return 1

    scraped_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    rows = [
        normalize_raw_row(row, source_path.name, scraped_at)
        for row in read_rows(source_path)
    ]
    write_rows(args.output, rows)
    print(f"Wrote {len(rows)} raw businesses to {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
