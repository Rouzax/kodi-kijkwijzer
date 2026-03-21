"""Backfill orchestration — processes media types and coordinates lookups."""

import logging
import os
import time

import requests

from kodi_kijkwijzer.config import load_overrides
from kodi_kijkwijzer.kodi import get_missing_ratings, update_rating
from kodi_kijkwijzer.media_types import MOVIE, TVSHOW
from kodi_kijkwijzer.providers import kijkwijzer, omdb, tmdb
from kodi_kijkwijzer.tracker import load_unresolved, save_unresolved, should_apply_fallback

log = logging.getLogger(__name__)


def backfill(config, movies_only=False, tvshows_only=False):
    """Main backfill logic."""
    kodi_cfg = config["kodi"]
    tmdb_key = config["tmdb"]["api_key"]
    omdb_key = config["omdb"]["api_key"]
    rating_cfg = config["rating"]
    opts = config.get("options", {})

    kodi_url = kodi_cfg["url"]
    auth = None
    if kodi_cfg.get("username"):
        auth = (kodi_cfg["username"], kodi_cfg.get("password", ""))

    prefix = rating_cfg.get("prefix", "")
    target = rating_cfg["target_country"]
    inference = rating_cfg.get("inference_countries", [])
    # Normalize mapping keys to strings (YAML parses unquoted numbers as integers)
    raw_mappings = rating_cfg.get("mappings", {})
    mappings = {c: {str(k): v for k, v in m.items()} for c, m in raw_mappings.items()}
    fallback_rating = rating_cfg.get("fallback_rating", "NR")
    dry_run = opts.get("dry_run", True)
    rate_limit = opts.get("rate_limit", 0.25)
    use_kijkwijzer = opts.get("kijkwijzer", True)
    retry_days = opts.get("retry_days", 30)

    # Load manual overrides
    overrides_path = opts.get("overrides_file", "overrides.yaml")
    overrides = load_overrides(overrides_path)
    if overrides:
        log.info("Loaded %d manual overrides from %s", len(overrides), overrides_path)

    # Determine unresolved directory from config
    unresolved_file = opts.get("unresolved_file", "unresolved.json")
    unresolved_dir = os.path.dirname(unresolved_file) or "."

    # Migrate legacy unresolved.json to per-type files
    legacy_path = os.path.join(unresolved_dir, "unresolved.json")
    movies_tracker = os.path.join(unresolved_dir, "unresolved_movies.json")
    if os.path.exists(legacy_path) and not os.path.exists(movies_tracker):
        log.info("Migrating legacy %s -> %s", legacy_path, movies_tracker)
        legacy_data = load_unresolved(legacy_path)
        if legacy_data and not dry_run:
            save_unresolved(movies_tracker, legacy_data, dry_run=False)

    media_types = []
    if not tvshows_only:
        media_types.append(MOVIE)
    if not movies_only:
        media_types.append(TVSHOW)

    all_stats = {}
    for media_type in media_types:
        stats = process_media_type(
            media_type, kodi_url, auth, tmdb_key, omdb_key,
            target, inference, mappings, prefix, fallback_rating,
            dry_run, rate_limit, use_kijkwijzer, retry_days,
            overrides, unresolved_dir,
        )
        all_stats[media_type.name] = stats

    return all_stats


def process_media_type(media_type, kodi_url, auth, tmdb_key, omdb_key,
                       target, inference, mappings, prefix, fallback_rating,
                       dry_run, rate_limit, use_kijkwijzer, retry_days,
                       overrides, unresolved_dir):
    """Process a single media type. This is the main loop."""
    # Load tracker for this media type
    tracker_path = os.path.join(unresolved_dir, f"unresolved_{media_type.name}s.json")
    unresolved = load_unresolved(tracker_path)

    items = get_missing_ratings(kodi_url, media_type, auth)
    log.info("=== %ss: found %d missing ratings (dry_run=%s) ===", media_type.label, len(items), dry_run)

    stats = {
        "tmdb_direct": 0, "tmdb_inferred": 0, "omdb": 0,
        "kijkwijzer": 0, "override": 0, "fallback": 0,
        "pending": 0, "error": 0,
    }

    for item in items:
        item_id = item["id"]
        title = item["title"]
        tmdb_id = item["tmdb_id"]
        imdb_id = item["imdb_id"]

        rating, source = None, None

        # Tier 0: Manual overrides
        if title in overrides:
            rating = overrides[title]
            source = "override"

        try:
            # Tier 1+2: TMDB direct + inference
            if not rating and tmdb_id and tmdb_key:
                rating, source = tmdb.lookup(
                    tmdb_id, tmdb_key, target, inference, mappings, media_type
                )
                time.sleep(rate_limit)

            # Tier 3: OMDB fallback
            if not rating and imdb_id and omdb_key:
                rating, source = omdb.lookup(imdb_id, omdb_key, mappings)
                time.sleep(rate_limit)

            # Tier 4: Kijkwijzer.nl scraping (handles its own rate limiting)
            if not rating and use_kijkwijzer:
                rating, source = kijkwijzer.lookup(title, rate_limit)

        except requests.RequestException as e:
            log.error("[%s] API error for '%s': %s", media_type.label, title, e)
            stats["error"] += 1
            continue

        if rating:
            # Resolved — remove from unresolved tracker if present
            unresolved.pop(title, None)
            full_rating = f"{prefix}{rating}"
            action = "DRY-RUN" if dry_run else "UPDATE"
            log.info("[%s] [%s] %-45s -> %s (source: %s)", media_type.label, action, title, full_rating, source)
            update_rating(kodi_url, item_id, full_rating, media_type, dry_run, auth)
            if source == "override":
                stats["override"] += 1
            elif source == "kijkwijzer":
                stats["kijkwijzer"] += 1
            elif source and "inferred" in source:
                stats["tmdb_inferred"] += 1
            elif source and source.startswith("tmdb"):
                stats["tmdb_direct"] += 1
            else:
                stats["omdb"] += 1
        elif fallback_rating and should_apply_fallback(title, unresolved, retry_days):
            # Retry window expired — apply fallback
            full_rating = f"{prefix}{fallback_rating}"
            action = "DRY-RUN" if dry_run else "UPDATE"
            log.info("[%s] [%s] %-45s -> %s (source: fallback, retry expired)", media_type.label, action, title, full_rating)
            update_rating(kodi_url, item_id, full_rating, media_type, dry_run, auth)
            unresolved.pop(title, None)
            stats["fallback"] += 1
        else:
            # Still in retry window or no fallback configured
            first_seen = unresolved.get(title, {}).get("first_seen", "today")
            log.info("[%s] [PENDING] %-45s -> waiting for rating (since %s)", media_type.label, title, first_seen)
            stats["pending"] += 1

    # Save unresolved tracker
    save_unresolved(tracker_path, unresolved, dry_run)
    if unresolved:
        log.info("Tracking %d unresolved %ss in %s", len(unresolved), media_type.label.lower(), tracker_path)

    log.info("--- %s Summary ---", media_type.label)
    log.info("Overrides:     %d", stats["override"])
    log.info("TMDB direct:   %d", stats["tmdb_direct"])
    log.info("TMDB inferred: %d", stats["tmdb_inferred"])
    log.info("OMDB:          %d", stats["omdb"])
    log.info("Kijkwijzer:    %d", stats["kijkwijzer"])
    log.info("Fallback:      %d", stats["fallback"])
    log.info("Pending:       %d", stats["pending"])
    log.info("Errors:        %d", stats["error"])
    return stats
