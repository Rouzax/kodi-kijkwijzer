"""Command-line interface for kodi-kijkwijzer."""

import argparse
import logging
import sys

from kodi_kijkwijzer.backfill import backfill
from kodi_kijkwijzer.config import load_config, validate_config

log = logging.getLogger(__name__)


def setup_logging(verbose=False, log_file=None):
    """Configure console and optional file logging."""
    fmt = "%(asctime)s %(levelname)-5s %(message)s"
    datefmt = "%H:%M:%S"
    level = logging.DEBUG if verbose else logging.INFO

    # Use UTF-8 for console and file to handle non-ASCII titles (e.g. Cyrillic)
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    console = logging.StreamHandler(sys.stdout)

    handlers = [console]
    if log_file:
        handlers.append(logging.FileHandler(log_file, encoding="utf-8"))

    logging.basicConfig(level=level, format=fmt, datefmt=datefmt, handlers=handlers)


def main():
    parser = argparse.ArgumentParser(
        description="Backfill missing age ratings in Kodi's video database."
    )
    parser.add_argument(
        "-c", "--config", default="config.yaml", help="Path to config file"
    )
    parser.add_argument(
        "--dry-run", action="store_true", default=None,
        help="Override config: log changes without writing to Kodi"
    )
    parser.add_argument(
        "--no-dry-run", action="store_true", default=None,
        help="Override config: actually write changes to Kodi"
    )
    parser.add_argument(
        "-v", "--verbose", action="store_true", help="Debug logging"
    )
    parser.add_argument(
        "-l", "--log-file", default=None,
        help="Write log output to file (in addition to console)"
    )

    # Media type filter (mutually exclusive)
    type_group = parser.add_mutually_exclusive_group()
    type_group.add_argument(
        "--movies-only", action="store_true",
        help="Only process movies, skip TV shows"
    )
    type_group.add_argument(
        "--tvshows-only", action="store_true",
        help="Only process TV shows, skip movies"
    )

    args = parser.parse_args()

    config = load_config(args.config)
    log_file = args.log_file or config.get("options", {}).get("log_file")
    setup_logging(verbose=args.verbose, log_file=log_file)

    errors = validate_config(config)
    if errors:
        for err in errors:
            log.error("Config error: %s", err)
        log.error("See config.example.yaml for reference")
        sys.exit(1)

    if args.dry_run:
        config.setdefault("options", {})["dry_run"] = True
    elif args.no_dry_run:
        config.setdefault("options", {})["dry_run"] = False

    backfill(config, movies_only=args.movies_only, tvshows_only=args.tvshows_only)
