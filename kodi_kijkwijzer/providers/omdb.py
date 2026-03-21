"""OMDB provider for rating lookups."""

import logging
import sys

import requests

log = logging.getLogger(__name__)


def lookup(imdb_id, api_key, mappings):
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
