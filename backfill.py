#!/usr/bin/env python3
"""Backfill missing age ratings in Kodi's video database."""

import argparse
import logging
import sys
import time

import mysql.connector
import requests
import yaml

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-5s %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger(__name__)


def load_config(path):
    with open(path) as f:
        return yaml.safe_load(f)


def get_movies_missing_rating(conn):
    """Return list of dicts with idMovie, title, tmdb_id, imdb_id."""
    cursor = conn.cursor(dictionary=True)
    cursor.execute("""
        SELECT m.idMovie, m.c00 AS title, m.c12 AS mpaa,
               u_tmdb.value AS tmdb_id, u_imdb.value AS imdb_id
        FROM movie m
        LEFT JOIN uniqueid u_tmdb ON u_tmdb.media_id = m.idMovie
            AND u_tmdb.media_type = 'movie' AND u_tmdb.type = 'tmdb'
        LEFT JOIN uniqueid u_imdb ON u_imdb.media_id = m.idMovie
            AND u_imdb.media_type = 'movie' AND u_imdb.type = 'imdb'
        WHERE m.c12 = '' OR m.c12 IS NULL
        ORDER BY m.c00
    """)
    return cursor.fetchall()


def lookup_tmdb(tmdb_id, api_key, target_country, inference_countries, mappings):
    """Query TMDB release_dates. Return (rating, source) or (None, None).

    Tries target_country first (direct match), then inference_countries
    with mapping to target scale.
    """
    url = f"https://api.themoviedb.org/3/movie/{tmdb_id}/release_dates"
    resp = requests.get(url, params={"api_key": api_key}, timeout=10)
    if resp.status_code != 200:
        log.warning("TMDB %s returned %s", tmdb_id, resp.status_code)
        return None, None

    results = resp.json().get("results", [])
    # Build country -> certification dict (take first non-empty cert)
    certs = {}
    for entry in results:
        country = entry["iso_3166_1"]
        for rd in entry.get("release_dates", []):
            if rd.get("certification"):
                certs[country] = rd["certification"]
                break

    # Direct match
    target = target_country.upper()
    if target in certs:
        return certs[target], f"tmdb-{target}"

    # Inference
    for country in inference_countries:
        country_upper = country.upper()
        if country_upper in certs and country_upper in mappings:
            foreign_rating = certs[country_upper]
            mapped = mappings[country_upper].get(foreign_rating)
            if mapped:
                return mapped, f"tmdb-inferred-{country_upper}"
            else:
                log.debug("No mapping for %s rating '%s'", country_upper, foreign_rating)

    return None, None


def lookup_omdb(imdb_id, api_key, mappings):
    """Query OMDB by IMDB ID. Return (rating, source) or (None, None).

    Maps the US MPAA 'Rated' field to target scale via US mapping.
    """
    resp = requests.get(
        "https://www.omdbapi.com/",
        params={"i": imdb_id, "apikey": api_key},
        timeout=10,
    )
    if resp.status_code != 200:
        log.warning("OMDB %s returned %s", imdb_id, resp.status_code)
        return None, None

    data = resp.json()
    if data.get("Response") == "False":
        log.debug("OMDB: no result for %s", imdb_id)
        return None, None

    rated = data.get("Rated", "")
    if not rated or rated == "N/A":
        return None, None

    us_mappings = mappings.get("US", {})
    mapped = us_mappings.get(rated)
    if mapped:
        return mapped, "omdb"

    log.debug("No US mapping for OMDB rating '%s'", rated)
    return None, None


def update_movie_rating(conn, movie_id, rating_value, dry_run=True):
    """Write rating to movie.c12. Returns True if updated."""
    if dry_run:
        return True
    cursor = conn.cursor()
    cursor.execute(
        "UPDATE movie SET c12 = %s WHERE idMovie = %s",
        (rating_value, movie_id),
    )
    conn.commit()
    return cursor.rowcount > 0


def backfill(config):
    """Main backfill logic."""
    db_cfg = config["db"]
    tmdb_key = config["tmdb"]["api_key"]
    omdb_key = config["omdb"]["api_key"]
    rating_cfg = config["rating"]
    opts = config.get("options", {})

    prefix = rating_cfg.get("prefix", "")
    target = rating_cfg["target_country"]
    inference = rating_cfg.get("inference_countries", [])
    mappings = rating_cfg.get("mappings", {})
    dry_run = opts.get("dry_run", True)
    rate_limit = opts.get("rate_limit", 0.25)

    conn = mysql.connector.connect(**db_cfg)
    movies = get_movies_missing_rating(conn)
    log.info("Found %d movies missing ratings (dry_run=%s)", len(movies), dry_run)

    stats = {"tmdb_direct": 0, "tmdb_inferred": 0, "omdb": 0, "not_found": 0, "error": 0}

    for movie in movies:
        mid = movie["idMovie"]
        title = movie["title"]
        tmdb_id = movie["tmdb_id"]
        imdb_id = movie["imdb_id"]

        rating, source = None, None

        try:
            # Tier 1+2: TMDB direct + inference
            if tmdb_id and tmdb_key:
                rating, source = lookup_tmdb(
                    tmdb_id, tmdb_key, target, inference, mappings
                )
                time.sleep(rate_limit)

            # Tier 3: OMDB fallback
            if not rating and imdb_id and omdb_key:
                rating, source = lookup_omdb(imdb_id, omdb_key, mappings)
                time.sleep(rate_limit)

        except requests.RequestException as e:
            log.error("API error for '%s': %s", title, e)
            stats["error"] += 1
            continue

        if rating:
            full_rating = f"{prefix}{rating}"
            action = "DRY-RUN" if dry_run else "UPDATE"
            log.info("[%s] %-45s -> %s (source: %s)", action, title, full_rating, source)
            update_movie_rating(conn, mid, full_rating, dry_run)
            if source and "inferred" in source:
                stats["tmdb_inferred"] += 1
            elif source and source.startswith("tmdb"):
                stats["tmdb_direct"] += 1
            else:
                stats["omdb"] += 1
        else:
            log.info("[SKIP]    %-45s -> no rating found", title)
            stats["not_found"] += 1

    conn.close()

    log.info("--- Summary ---")
    log.info("TMDB direct:   %d", stats["tmdb_direct"])
    log.info("TMDB inferred: %d", stats["tmdb_inferred"])
    log.info("OMDB:          %d", stats["omdb"])
    log.info("Not found:     %d", stats["not_found"])
    log.info("Errors:        %d", stats["error"])
    return stats


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "-c", "--config", default="config.yaml", help="Path to config file"
    )
    parser.add_argument(
        "--dry-run", action="store_true", default=None,
        help="Override config: log changes without writing to DB"
    )
    parser.add_argument(
        "--no-dry-run", action="store_true", default=None,
        help="Override config: actually write changes to DB"
    )
    parser.add_argument(
        "-v", "--verbose", action="store_true", help="Debug logging"
    )
    args = parser.parse_args()

    if args.verbose:
        logging.getLogger().setLevel(logging.DEBUG)

    config = load_config(args.config)

    if args.dry_run:
        config.setdefault("options", {})["dry_run"] = True
    elif args.no_dry_run:
        config.setdefault("options", {})["dry_run"] = False

    backfill(config)


if __name__ == "__main__":
    main()
