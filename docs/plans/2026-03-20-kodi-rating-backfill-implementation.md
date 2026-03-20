# Kodi Rating Backfill — Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Build a Python CLI tool that backfills missing age ratings in a Kodi MariaDB database using TMDB and OMDB APIs with cross-country inference.

**Architecture:** Single-file Python script (`backfill.py`) reads YAML config, queries MariaDB for movies with empty `c12` (MPAA) column, resolves ratings via TMDB release_dates API then OMDB fallback, maps foreign ratings to the target country's scale, and writes back to the DB. Dry-run mode by default.

**Tech Stack:** Python 3, requests, PyYAML, mysql-connector-python

---

### Task 1: Project scaffolding

**Files:**
- Create: `requirements.txt`
- Create: `config.example.yaml`
- Create: `.gitignore`

**Step 1: Create requirements.txt**

```
requests>=2.28
PyYAML>=6.0
mysql-connector-python>=8.0
```

**Step 2: Create config.example.yaml**

```yaml
db:
  host: localhost
  port: 3306
  user: kodi
  password: ""
  database: MyVideos131

tmdb:
  api_key: ""

omdb:
  api_key: ""

rating:
  prefix: "Rated "
  target_country: "NL"
  inference_countries:
    - BE    # Belgium — same Kijkwijzer system
    - DE    # Germany — FSK, very similar
    - AT    # Austria — similar to DE
    - FR    # France — geographically close
    - GB    # UK — BBFC, decent middle ground
    - DK    # Denmark — Nordic neighbor
    - SE    # Sweden — Nordic, similar values
    - US    # Last resort — different philosophy
  mappings:
    BE:
      "AL": "AL"
      "6": "6"
      "9": "9"
      "12": "12"
      "14": "14"
      "16": "16"
      "18": "18"
    DE:
      "0": "AL"
      "6": "6"
      "12": "12"
      "16": "16"
      "18": "18"
    AT:
      "0": "AL"
      "6": "6"
      "10": "9"
      "12": "12"
      "14": "14"
      "16": "16"
      "18": "18"
    FR:
      "U": "AL"
      "10": "9"
      "12": "12"
      "16": "16"
      "18": "18"
    GB:
      "U": "AL"
      "PG": "6"
      "12A": "12"
      "12": "12"
      "15": "16"
      "18": "18"
    DK:
      "A": "AL"
      "7": "6"
      "11": "12"
      "15": "16"
    SE:
      "Btl": "AL"
      "7": "6"
      "11": "12"
      "15": "16"
    US:
      "G": "AL"
      "PG": "6"
      "PG-13": "12"
      "R": "16"
      "NC-17": "18"

options:
  dry_run: true
  rate_limit: 0.25
```

**Step 3: Create .gitignore**

```
config.yaml
__pycache__/
*.pyc
.env
venv/
```

**Step 4: Initialize git and commit**

```bash
cd /home/martijn/kijkwijzer
git init
git add requirements.txt config.example.yaml .gitignore docs/
git commit -m "chore: project scaffolding with config template and dependencies"
```

---

### Task 2: Config loading and DB connection

**Files:**
- Create: `backfill.py`
- Test: manual — run against test DB

**Step 1: Write config loader and DB query**

```python
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
```

**Step 2: Run to verify DB connection**

Create a test config at `config.yaml` with test DB credentials and run:

```bash
python3 -c "
from backfill import load_config, get_movies_missing_rating
import mysql.connector
cfg = load_config('config.yaml')
conn = mysql.connector.connect(**cfg['db'])
movies = get_movies_missing_rating(conn)
print(f'Found {len(movies)} movies missing ratings')
for m in movies[:3]:
    print(f'  {m[\"title\"]} tmdb={m[\"tmdb_id\"]} imdb={m[\"imdb_id\"]}')
conn.close()
"
```

Expected: `Found 53 movies missing ratings` with title/ID output.

**Step 3: Commit**

```bash
git add backfill.py
git commit -m "feat: config loading and DB query for movies missing ratings"
```

---

### Task 3: TMDB rating lookup

**Files:**
- Modify: `backfill.py`

**Step 1: Add TMDB lookup function**

Append to `backfill.py`:

```python
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
```

**Step 2: Test against a known movie**

```bash
python3 -c "
from backfill import load_config, lookup_tmdb
cfg = load_config('config.yaml')
rating, source = lookup_tmdb(
    '9444',  # Anastasia
    cfg['tmdb']['api_key'],
    cfg['rating']['target_country'],
    cfg['rating']['inference_countries'],
    cfg['rating']['mappings']
)
print(f'Rating: {rating}, Source: {source}')
"
```

Expected: a rating from one of the configured countries (likely US mapping).

**Step 3: Commit**

```bash
git add backfill.py
git commit -m "feat: TMDB release_dates lookup with country inference"
```

---

### Task 4: OMDB rating lookup

**Files:**
- Modify: `backfill.py`

**Step 1: Add OMDB lookup function**

Append to `backfill.py`:

```python
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
```

**Step 2: Test against a known movie**

```bash
python3 -c "
from backfill import load_config, lookup_omdb
cfg = load_config('config.yaml')
rating, source = lookup_omdb('tt0118617', cfg['omdb']['api_key'], cfg['rating']['mappings'])
print(f'Rating: {rating}, Source: {source}')
"
```

Expected: a mapped rating (Anastasia is rated G in US → should map to AL).

**Step 3: Commit**

```bash
git add backfill.py
git commit -m "feat: OMDB fallback lookup with US MPAA mapping"
```

---

### Task 5: DB update and main loop

**Files:**
- Modify: `backfill.py`

**Step 1: Add update function and main loop**

Append to `backfill.py`:

```python
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
                if rating:
                    time.sleep(rate_limit)

            # Tier 3: OMDB fallback
            if not rating and imdb_id and omdb_key:
                rating, source = lookup_omdb(imdb_id, omdb_key, mappings)
                if rating:
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
```

**Step 2: Dry-run test against test DB**

```bash
python3 backfill.py -c config.yaml --dry-run -v
```

Expected: all 53 movies processed, each logged with `[DRY-RUN]` and a rating+source, summary at end.

**Step 3: Commit**

```bash
git add backfill.py
git commit -m "feat: main backfill loop with dry-run, CLI args, and summary stats"
```

---

### Task 6: Live test and README

**Files:**
- Create: `README.md`

**Step 1: Run live against test DB**

```bash
python3 backfill.py -c config.yaml --no-dry-run
```

Expected: 53 movies updated, verify with:

```bash
python3 -c "
import mysql.connector
conn = mysql.connector.connect(host='docker.home.lan', port=3306, user='kodi', password='kodi', database='testvideo131')
cur = conn.cursor()
cur.execute('SELECT COUNT(*) FROM movie WHERE c12 = \"\" OR c12 IS NULL')
print(f'Still missing: {cur.fetchone()[0]}')
cur.execute('SELECT c00, c12 FROM movie WHERE c00 IN (\"Anastasia\", \"Bee Movie\", \"A Goofy Movie\") ORDER BY c00')
for row in cur.fetchall():
    print(f'  {row[0]}: {row[1]}')
conn.close()
"
```

**Step 2: Write README.md**

Include: what it does, quick start, config reference, example output, how inference works, contributing notes.

**Step 3: Commit**

```bash
git add README.md
git commit -m "docs: README with usage, config reference, and inference explanation"
```

---

### Task 7: GitHub repository setup

**Step 1: Create GitHub repo**

```bash
gh repo create kodi-rating-backfill --public --description "Backfill missing age ratings in Kodi video database using TMDB and OMDB" --source .
```

**Step 2: Push**

```bash
git push -u origin main
```

---

## Task Dependencies

```
Task 1 (scaffolding)
  └─> Task 2 (config + DB query)
        └─> Task 3 (TMDB lookup)
        └─> Task 4 (OMDB lookup)
              └─> Task 5 (main loop + CLI)
                    └─> Task 6 (live test + README)
                          └─> Task 7 (GitHub)
```

Tasks 3 and 4 are independent and can be done in parallel.
