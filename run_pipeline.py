"""
run_pipeline.py  —  Pipeline Orchestrator
------------------------------------------
Chains scraper → validator → upload for a given domain + location.

CLI examples:
    python run_pipeline.py --domain restaurant --location Bhopal
    python run_pipeline.py --domain gym --location Delhi --sources google linkedin facebook
    python run_pipeline.py --domain salon --location Pune --count 30 --skip-upload
"""

import argparse
import re
import subprocess
import sys
from pathlib import Path


def run_step(command: list):
    print(f"\n> {' '.join(command)}")
    result = subprocess.run(command, check=False)
    if result.returncode != 0:
        raise SystemExit(result.returncode)


def safe_slug(text: str) -> str:
    return re.sub(r"[^\w]", "_", text.strip().lower())


def main():
    parser = argparse.ArgumentParser(description="Run the lead generation pipeline.")
    parser.add_argument("--domain",   required=True,
                        help='Business type, e.g. "restaurant", "gym", "salon"')
    parser.add_argument("--location", required=True,
                        help='City, e.g. "Bhopal", "Delhi"')
    parser.add_argument("--sources",  nargs="+",
                        default=["google"],
                        choices=["google", "linkedin", "facebook", "reddit", "justdial"],
                        help="Platforms to search (default: google)")
    parser.add_argument("--count",    type=int, default=50,
                        help="Max leads to collect (default: 50)")
    parser.add_argument("--skip-scrape",  action="store_true")
    parser.add_argument("--skip-upload",  action="store_true")

    # Derived paths — auto-named by domain+location
    args = parser.parse_args()

    slug = f"{safe_slug(args.domain)}_{safe_slug(args.location)}"
    raw_output      = f"data/raw_{slug}.csv"
    clean_output    = f"data/clean_{slug}.csv"
    rejected_output = f"data/rejected_{slug}.csv"

    Path("data").mkdir(exist_ok=True)

    # ── Step 1: Scrape ────────────────────────────────────────────────────────
    if not args.skip_scrape:
        run_step([
            sys.executable, "scraper.py",
            "--domain",   args.domain,
            "--location", args.location,
            "--sources",  *args.sources,
            "--count",    str(args.count),
            "--output",   raw_output,
        ])

    # ── Step 2: Validate & Score ──────────────────────────────────────────────
    run_step([
        sys.executable, "validator.py",
        "--input",           raw_output,
        "--output",          clean_output,
        "--rejected-output", rejected_output,
    ])

    # ── Step 3: Upload ────────────────────────────────────────────────────────
    if not args.skip_upload:
        run_step([
            sys.executable, "upload.py",
            "--input",  clean_output,
            "--domain", args.domain,
        ])

    print("\n✅  Pipeline finished.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
