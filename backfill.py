#!/usr/bin/env python3
"""Backfill missing age ratings in Kodi's video database."""

import argparse
import json
import logging
import re
import sys
import time
from datetime import date

import requests
import yaml

log = logging.getLogger(__name__)


def load_config(path):
    try:
        with open(path) as f:
            config = yaml.safe_load(f)
    except FileNotFoundError:
        print(f"Error: Config file not found: {path}")
        print("Copy config.example.yaml to config.yaml and edit it.")
        sys.exit(1)
    except yaml.YAMLError as e:
        print(f"Error: Invalid YAML in {path}: {e}")
        sys.exit(1)
    if not isinstance(config, dict):
        print(f"Error: Config file {path} is empty or invalid")
        sys.exit(1)
    return config


def load_overrides(path):
    """Load manual rating overrides from YAML file. Returns dict of title -> rating."""
    try:
        with open(path) as f:
            data = yaml.safe_load(f) or {}
            return data.get("overrides", {})
    except FileNotFoundError:
        return {}


def load_unresolved(path):
    """Load unresolved tracker. Returns dict of title -> {"first_seen": "YYYY-MM-DD"}."""
    try:
        with open(path) as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def save_unresolved(path, data, dry_run=True):
    """Save unresolved tracker to JSON file."""
    if dry_run:
        return
    try:
        with open(path, "w") as f:
            json.dump(data, f, indent=2, sort_keys=True)
    except OSError as e:
        log.warning("Could not save unresolved tracker to %s: %s", path, e)


def should_apply_fallback(title, unresolved, retry_days):
    """Check if a movie has been unresolved long enough to apply fallback.

    Returns True if fallback should be applied, False if still in retry window.
    Also adds the movie to the tracker if not yet tracked.
    """
    today = date.today().isoformat()

    if title not in unresolved:
        unresolved[title] = {"first_seen": today}
        log.debug("Tracking new unresolved: '%s'", title)
        return False

    try:
        first_seen = date.fromisoformat(unresolved[title]["first_seen"])
    except (KeyError, ValueError):
        # Corrupt entry — reset tracking
        unresolved[title] = {"first_seen": today}
        log.warning("Reset corrupt unresolved entry for '%s'", title)
        return False
    days_elapsed = (date.today() - first_seen).days

    if days_elapsed >= retry_days:
        log.debug("Retry window expired for '%s' (%d days)", title, days_elapsed)
        return True

    log.debug("Still in retry window for '%s' (%d/%d days)", title, days_elapsed, retry_days)
    return False


def get_movies_missing_rating(url, auth=None):
    """Query Kodi JSON-RPC for all movies, return those with empty mpaa.

    Kodi's server-side filter for empty mpaa doesn't work reliably,
    so we fetch all movies and filter client-side.
    """
    payload = {
        "jsonrpc": "2.0",
        "method": "VideoLibrary.GetMovies",
        "params": {
            "properties": ["mpaa", "uniqueid", "title"],
            "sort": {"method": "title"},
        },
        "id": 1,
    }
    try:
        resp = requests.post(url, json=payload, auth=auth, timeout=30)
        resp.raise_for_status()
    except requests.ConnectionError:
        log.error("Cannot connect to Kodi at %s — is the web server enabled?", url)
        sys.exit(1)
    except requests.Timeout:
        log.error("Connection to Kodi at %s timed out", url)
        sys.exit(1)
    except requests.HTTPError as e:
        log.error("Kodi returned HTTP error: %s", e)
        sys.exit(1)

    try:
        data = resp.json()
    except ValueError:
        log.error("Kodi returned invalid JSON — is %s the correct JSON-RPC endpoint?", url)
        sys.exit(1)

    if "error" in data:
        err = data["error"]
        msg = err.get("message", err) if isinstance(err, dict) else err
        log.error("Kodi JSON-RPC error: %s", msg)
        sys.exit(1)

    all_movies = data.get("result", {}).get("movies", [])
    if not all_movies:
        log.warning("No movies found in Kodi library at %s", url)

    return [
        {
            "idMovie": m["movieid"],
            "title": m["title"],
            "tmdb_id": m.get("uniqueid", {}).get("tmdb"),
            "imdb_id": m.get("uniqueid", {}).get("imdb"),
        }
        for m in all_movies
        if not m.get("mpaa")
    ]


def update_movie_rating(url, movie_id, rating_value, dry_run=True, auth=None):
    """Set mpaa via Kodi JSON-RPC SetMovieDetails."""
    if dry_run:
        return True
    payload = {
        "jsonrpc": "2.0",
        "method": "VideoLibrary.SetMovieDetails",
        "params": {"movieid": movie_id, "mpaa": rating_value},
        "id": 1,
    }
    try:
        resp = requests.post(url, json=payload, auth=auth, timeout=10)
        resp.raise_for_status()
        result = resp.json().get("result")
        if result != "OK":
            log.warning("Failed to set rating for movieid %s: %s", movie_id, result)
            return False
        return True
    except requests.RequestException as e:
        log.warning("Failed to set rating for movieid %s: %s", movie_id, e)
        return False


_kijkwijzer_session = None


def _get_kijkwijzer_session():
    """Get a reusable requests session with a descriptive User-Agent."""
    global _kijkwijzer_session
    if _kijkwijzer_session is None:
        _kijkwijzer_session = requests.Session()
        _kijkwijzer_session.headers["User-Agent"] = (
            "Kodi-Rating-Backfill/1.0 (https://github.com/kodi-rating-backfill)"
        )
    return _kijkwijzer_session


def lookup_kijkwijzer(title, rate_limit=0.25):
    """Search kijkwijzer.nl by title, scrape the detail page for age rating.

    Returns (rating, source) or (None, None).
    Rating values: 'AL', '6', '9', '12', '14', '16', '18'.
    """
    session = _get_kijkwijzer_session()
    search_url = "https://www.kijkwijzer.nl/zoeken/"
    try:
        resp = session.get(
            search_url,
            params={"query": title, "producties": "0"},
            timeout=10,
        )
        if resp.status_code != 200:
            log.warning("Kijkwijzer search returned %s", resp.status_code)
            return None, None
    except requests.RequestException as e:
        log.warning("Kijkwijzer search failed: %s", e)
        return None, None

    # Extract film/series links from search results
    links = re.findall(
        r'href="(https://www\.kijkwijzer\.nl/(?:films|series|overige)/[^"]+/)"',
        resp.text,
    )
    if not links:
        log.debug("Kijkwijzer: no results for '%s'", title)
        return None, None

    # Try to find an exact or close title match from the links
    title_lower = title.lower().strip()
    # URL slugs use hyphens, strip punctuation for comparison
    title_slug = re.sub(r"[^a-z0-9]+", "-", title_lower).strip("-")

    best_link = None
    for link in links:
        # Extract slug from URL
        slug_match = re.search(r"/([^/]+)/$", link)
        if not slug_match:
            continue
        slug = slug_match.group(1)
        # Remove trailing numbers that kijkwijzer adds for duplicates (e.g. "film-1")
        slug_base = re.sub(r"-\d+$", "", slug)
        if slug == title_slug or slug_base == title_slug:
            best_link = link
            break
        # Partial match: require at least 60% overlap to avoid false positives
        # (e.g. "ice" matching "ice-age")
        if slug.startswith(title_slug):
            best_link = link
            break
        if title_slug.startswith(slug_base) and len(slug_base) >= len(title_slug) * 0.6:
            best_link = link
            break

    if not best_link:
        log.debug("Kijkwijzer: no title match for '%s' (slug: %s)", title, title_slug)
        return None, None

    log.info("Kijkwijzer: matched '%s' -> %s", title, best_link)
    time.sleep(rate_limit)

    # Fetch detail page
    try:
        detail_resp = session.get(best_link, timeout=10)
        if detail_resp.status_code != 200:
            return None, None
    except requests.RequestException:
        return None, None

    detail_text = detail_resp.text

    # Parse rating from detail page
    # Check specific pattern first (more reliable than "Alle leeftijden")
    age_match = re.search(r"schadelijk tot (\d+) jaar", detail_text)
    if age_match:
        age = age_match.group(1)
        if age in ("6", "9", "12", "14", "16", "18"):
            return age, "kijkwijzer"

    # "Alle leeftijden" — only match in heading context to avoid nav/footer matches
    if re.search(r"<h[12][^>]*>.*?Alle leeftijden", detail_text, re.DOTALL):
        return "AL", "kijkwijzer"
    # Fallback: match if it appears in the main content area (not just anywhere)
    if detail_text.count("Alle leeftijden") >= 2:
        # Multiple occurrences suggest it's the actual rating, not just a nav item
        return "AL", "kijkwijzer"

    log.debug("Kijkwijzer: could not parse rating from %s", best_link)
    return None, None


def lookup_tmdb(tmdb_id, api_key, target_country, inference_countries, mappings):
    """Query TMDB release_dates. Return (rating, source) or (None, None).

    Tries target_country first (direct match), then inference_countries
    with mapping to target scale.
    """
    url = f"https://api.themoviedb.org/3/movie/{tmdb_id}/release_dates"
    try:
        resp = requests.get(url, params={"api_key": api_key}, timeout=10)
    except requests.RequestException as e:
        log.warning("TMDB request failed for %s: %s", tmdb_id, e)
        return None, None
    if resp.status_code == 401:
        log.error("TMDB API key is invalid (401 Unauthorized)")
        sys.exit(1)
    if resp.status_code != 200:
        log.warning("TMDB %s returned %s", tmdb_id, resp.status_code)
        return None, None

    try:
        results = resp.json().get("results", [])
    except ValueError:
        log.warning("TMDB returned invalid JSON for %s", tmdb_id)
        return None, None
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
    try:
        resp = requests.get(
            "https://www.omdbapi.com/",
            params={"i": imdb_id, "apikey": api_key},
            timeout=10,
        )
    except requests.RequestException as e:
        log.warning("OMDB request failed for %s: %s", imdb_id, e)
        return None, None
    if resp.status_code == 401:
        log.error("OMDB API key is invalid (401 Unauthorized)")
        sys.exit(1)
    if resp.status_code != 200:
        log.warning("OMDB %s returned %s", imdb_id, resp.status_code)
        return None, None

    try:
        data = resp.json()
    except ValueError:
        log.warning("OMDB returned invalid JSON for %s", imdb_id)
        return None, None
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


def backfill(config):
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

    # Load unresolved tracker
    unresolved_path = opts.get("unresolved_file", "unresolved.json")
    unresolved = load_unresolved(unresolved_path)

    movies = get_movies_missing_rating(kodi_url, auth)
    log.info("Found %d movies missing ratings (dry_run=%s)", len(movies), dry_run)

    stats = {
        "tmdb_direct": 0, "tmdb_inferred": 0, "omdb": 0,
        "kijkwijzer": 0, "override": 0, "fallback": 0,
        "pending": 0, "error": 0,
    }

    for movie in movies:
        mid = movie["idMovie"]
        title = movie["title"]
        tmdb_id = movie["tmdb_id"]
        imdb_id = movie["imdb_id"]

        rating, source = None, None

        # Tier 0: Manual overrides
        if title in overrides:
            rating = overrides[title]
            source = "override"

        try:
            # Tier 1+2: TMDB direct + inference
            if not rating and tmdb_id and tmdb_key:
                rating, source = lookup_tmdb(
                    tmdb_id, tmdb_key, target, inference, mappings
                )
                time.sleep(rate_limit)

            # Tier 3: OMDB fallback
            if not rating and imdb_id and omdb_key:
                rating, source = lookup_omdb(imdb_id, omdb_key, mappings)
                time.sleep(rate_limit)

            # Tier 4: Kijkwijzer.nl scraping (handles its own rate limiting)
            if not rating and use_kijkwijzer:
                rating, source = lookup_kijkwijzer(title, rate_limit)

        except requests.RequestException as e:
            log.error("API error for '%s': %s", title, e)
            stats["error"] += 1
            continue

        if rating:
            # Resolved — remove from unresolved tracker if present
            unresolved.pop(title, None)
            full_rating = f"{prefix}{rating}"
            action = "DRY-RUN" if dry_run else "UPDATE"
            log.info("[%s] %-45s -> %s (source: %s)", action, title, full_rating, source)
            update_movie_rating(kodi_url, mid, full_rating, dry_run, auth)
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
            log.info("[%s] %-45s -> %s (source: fallback, retry expired)", action, title, full_rating)
            update_movie_rating(kodi_url, mid, full_rating, dry_run, auth)
            unresolved.pop(title, None)
            stats["fallback"] += 1
        else:
            # Still in retry window or no fallback configured
            first_seen = unresolved.get(title, {}).get("first_seen", "today")
            log.info("[PENDING] %-45s -> waiting for rating (since %s)", title, first_seen)
            stats["pending"] += 1

    # Save unresolved tracker
    save_unresolved(unresolved_path, unresolved, dry_run)
    if unresolved:
        log.info("Tracking %d unresolved movies in %s", len(unresolved), unresolved_path)

    log.info("--- Summary ---")
    log.info("Overrides:     %d", stats["override"])
    log.info("TMDB direct:   %d", stats["tmdb_direct"])
    log.info("TMDB inferred: %d", stats["tmdb_inferred"])
    log.info("OMDB:          %d", stats["omdb"])
    log.info("Kijkwijzer:    %d", stats["kijkwijzer"])
    log.info("Fallback:      %d", stats["fallback"])
    log.info("Pending:       %d", stats["pending"])
    log.info("Errors:        %d", stats["error"])
    return stats


def validate_config(config):
    """Check required config keys exist. Returns list of errors."""
    errors = []
    if not config.get("kodi", {}).get("url"):
        errors.append("kodi.url is required")
    if not config.get("tmdb", {}).get("api_key"):
        errors.append("tmdb.api_key is required")
    if not config.get("omdb", {}).get("api_key"):
        errors.append("omdb.api_key is required")
    if not config.get("rating", {}).get("target_country"):
        errors.append("rating.target_country is required")
    return errors


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
    parser = argparse.ArgumentParser(description=__doc__)
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

    backfill(config)


if __name__ == "__main__":
    main()
