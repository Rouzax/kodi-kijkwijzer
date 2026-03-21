"""Kodi JSON-RPC communication."""

import logging
import sys

import requests

log = logging.getLogger(__name__)


def get_missing_ratings(url, media_type, auth=None):
    """Query Kodi JSON-RPC for items with empty mpaa.

    Kodi's server-side filter for empty mpaa doesn't work reliably,
    so we fetch all items and filter client-side.
    """
    payload = {
        "jsonrpc": "2.0",
        "method": media_type.kodi_list_method,
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

    all_items = data.get("result", {}).get(media_type.kodi_result_key, [])
    if not all_items:
        log.warning("No %ss found in Kodi library at %s", media_type.label.lower(), url)

    return [
        {
            "id": item[media_type.kodi_id_field],
            "title": item["title"],
            "tmdb_id": item.get("uniqueid", {}).get("tmdb"),
            "imdb_id": item.get("uniqueid", {}).get("imdb"),
        }
        for item in all_items
        if not item.get("mpaa")
    ]


def update_rating(url, item_id, rating_value, media_type, dry_run=True, auth=None):
    """Set mpaa via Kodi JSON-RPC."""
    if dry_run:
        return True
    payload = {
        "jsonrpc": "2.0",
        "method": media_type.kodi_set_method,
        "params": {media_type.kodi_id_field: item_id, "mpaa": rating_value},
        "id": 1,
    }
    try:
        resp = requests.post(url, json=payload, auth=auth, timeout=10)
        resp.raise_for_status()
        result = resp.json().get("result")
        if result != "OK":
            log.warning("Failed to set rating for %s %s: %s", media_type.kodi_id_field, item_id, result)
            return False
        return True
    except requests.RequestException as e:
        log.warning("Failed to set rating for %s %s: %s", media_type.kodi_id_field, item_id, e)
        return False
