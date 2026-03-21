"""Kijkwijzer.nl scraping provider."""

import logging
import re
import time

import requests

log = logging.getLogger(__name__)

_kijkwijzer_session = None


def _get_kijkwijzer_session():
    """Get a reusable requests session with a descriptive User-Agent."""
    global _kijkwijzer_session
    if _kijkwijzer_session is None:
        _kijkwijzer_session = requests.Session()
        _kijkwijzer_session.headers["User-Agent"] = (
            "Kodi-Kijkwijzer/2.0 (https://github.com/Rouzax/kodi-kijkwijzer)"
        )
    return _kijkwijzer_session


def lookup(title, rate_limit=0.25):
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
