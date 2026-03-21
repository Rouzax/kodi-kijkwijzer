"""TMDB provider for certification lookups."""

import logging
import sys

import requests

log = logging.getLogger(__name__)


def lookup(tmdb_id, api_key, target_country, inference_countries, mappings, media_type):
    """Query TMDB for certifications. Return (rating, source) or (None, None).

    Tries target_country first (direct match), then inference_countries
    with mapping to target scale.
    """
    url = f"https://api.themoviedb.org/3{media_type.tmdb_endpoint.format(id=tmdb_id)}"
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

    certs = media_type.tmdb_parse_certs(results)

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
