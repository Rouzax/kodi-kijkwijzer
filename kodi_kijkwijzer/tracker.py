"""Unresolved items tracker — tracks items waiting for ratings."""

import json
import logging
from datetime import date

log = logging.getLogger(__name__)


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
    """Check if an item has been unresolved long enough to apply fallback.

    Returns True if fallback should be applied, False if still in retry window.
    Also adds the item to the tracker if not yet tracked.
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
