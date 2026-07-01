import argparse
import subprocess
import sys


def run_step(command):
    print(f"\n> {' '.join(command)}")
    result = subprocess.run(command, check=False)
    if result.returncode != 0:
        raise SystemExit(result.returncode)


def main():
    parser = argparse.ArgumentParser(description="Run the restaurant lead pipeline.")
    parser.add_argument("--raw-source", default="businesses.csv", help="Source CSV used by scraper.py.")
    parser.add_argument("--raw-output", default="data/raw_businesses.csv", help="Raw CSV output path.")
    parser.add_argument("--clean-output", default="data/clean_businesses.csv", help="Clean CSV output path.")
    parser.add_argument("--rejected-output", default="data/rejected_businesses.csv", help="Rejected CSV output path.")
    parser.add_argument("--skip-scrape", action="store_true", help="Reuse the existing raw CSV.")
    parser.add_argument("--skip-upload", action="store_true", help="Stop after validation.")
    args = parser.parse_args()

    if not args.skip_scrape:
        run_step([
            sys.executable,
            "scraper.py",
            "--source",
            args.raw_source,
            "--output",
            args.raw_output,
        ])

    run_step([
        sys.executable,
        "validator.py",
        "--input",
        args.raw_output,
        "--output",
        args.clean_output,
        "--rejected-output",
        args.rejected_output,
    ])

    if not args.skip_upload:
        run_step([sys.executable, "upload.py", "--input", args.clean_output])

    print("\nPipeline finished.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
