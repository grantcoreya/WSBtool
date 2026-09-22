from __future__ import annotations

import argparse
import json
from pathlib import Path

from scraper.source import LeagueSecretaryScraper


def main() -> None:
    parser = argparse.ArgumentParser(description="Fetch Rainbowlers League reports from League Secretary.")
    parser.add_argument("--output", type=Path, default=Path("data"), help="Directory for collected data.")
    parser.add_argument("--backfill", action="store_true", help="Fetch all discovered supported reports.")
    parser.add_argument("--verbose", action="store_true", help="Enable verbose logging.")

    args = parser.parse_args()

    if args.verbose:
        import logging
        logging.basicConfig(level=logging.INFO, format="%(levelname)s:%(message)s")

    scraper = LeagueSecretaryScraper(output_dir=args.output)
    result = scraper.run(backfill=args.backfill)

    print(json.dumps(result, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
