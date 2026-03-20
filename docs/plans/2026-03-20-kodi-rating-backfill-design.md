# Kodi Rating Backfill Tool — Design Document

## Problem

Kodi's TMDB scraper plugin only fetches age ratings for the configured certification country. When TMDB has no certification for that country, the movie's MPAA field (`c12`) is left empty. There is no fallback to other countries or other data sources.

This tool backfills those missing ratings by querying TMDB and OMDB, with cross-country inference when the target country's rating is unavailable.

## Data Sources (in priority order)

1. **TMDB `/movie/{id}/release_dates`** — target country certification (e.g. NL)
2. **TMDB same endpoint** — infer from other countries (configurable priority: DE, GB, FR, US, etc.) mapped to target country's rating scale
3. **OMDB `/?i={imdb_id}`** — US MPAA rating as final fallback, mapped to target scale

## Kodi Database Schema (verified against source + test DB)

- **DB**: MariaDB, schema version 144, database name like `MyVideosNNN`
- **Table**: `movie` — column `c12` stores the MPAA rating string (e.g. `"Rated 12"`, `"Rated R"`)
- **Table**: `uniqueid` — stores TMDB and IMDB IDs per movie
  - Joined via: `uniqueid.media_id = movie.idMovie AND uniqueid.media_type = 'movie'`
  - Types: `tmdb` (numeric ID) and `imdb` (tt-prefixed ID)
- **Empty rating**: `c12 = ""` (empty string, not NULL)

### Query to find movies needing backfill

```sql
SELECT m.idMovie, m.c00 AS title, m.c12 AS mpaa,
       u_tmdb.value AS tmdb_id, u_imdb.value AS imdb_id
FROM movie m
LEFT JOIN uniqueid u_tmdb ON u_tmdb.media_id = m.idMovie
    AND u_tmdb.media_type = 'movie' AND u_tmdb.type = 'tmdb'
LEFT JOIN uniqueid u_imdb ON u_imdb.media_id = m.idMovie
    AND u_imdb.media_type = 'movie' AND u_imdb.type = 'imdb'
WHERE m.c12 = '' OR m.c12 IS NULL
```

## Rating Inference Mapping

Conservative approach — always rounds up to the stricter bracket.

### Kijkwijzer (NL) target scale

`AL, 6, 9, 12, 14, 16, 18`

### Country mappings (configurable in config.yaml)

Priority order: culturally closest to NL first, US last (different philosophy — conservative on nudity, lenient on violence).

| Source | Rating → NL equivalent |
|--------|----------------------|
| **BE (Kijkwijzer)** | AL→AL, 6→6, 9→9, 12→12, 14→14, 16→16, 18→18 (same system!) |
| **DE (FSK)** | 0→AL, 6→6, 12→12, 16→16, 18→18 |
| **AT (ABMC)** | 0→AL, 6→6, 10→9, 12→12, 14→14, 16→16, 18→18 |
| **FR (CNC)** | U→AL, 10→9, 12→12, 16→16, 18→18 |
| **GB (BBFC)** | U→AL, PG→6, 12A→12, 12→12, 15→16, 18→18 |
| **DK (Medierådet)** | A→AL, 7→6, 11→12, 15→16 |
| **SE (Swedish Media Council)** | Btl→AL, 7→6, 11→12, 15→16 |
| **US (MPAA)** | G→AL, PG→6, PG-13→12, R→16, NC-17→18, NR→(skip) |

When inferring, use the **first match** in the configured country priority list. This avoids averaging across systems with different philosophies.

## Configuration (`config.yaml`)

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

## Script Flow

```
1. Load config.yaml
2. Connect to MariaDB
3. Query movies with empty c12 (get idMovie, title, tmdb_id, imdb_id)
4. For each movie:
   a. If tmdb_id exists:
      - GET /movie/{tmdb_id}/release_dates
      - Check for target_country cert → use directly
      - If missing → iterate inference_countries, map first match
   b. If still no rating and imdb_id exists:
      - GET omdbapi.com/?i={imdb_id}
      - Map the Rated field via US mapping
   c. If rating found:
      - UPDATE movie SET c12 = '{prefix}{rating}' WHERE idMovie = {id}
   d. Log: movie title, source used (tmdb-direct/tmdb-inferred-DE/omdb), rating
   e. Rate limit between API calls
5. Print summary: filled (by source) / not found
```

## Project Structure

```
kodi-rating-backfill/
├── backfill.py              # single-file script
├── config.example.yaml      # template config (no secrets)
├── requirements.txt         # requests, pyyaml, mysql-connector-python
├── README.md
├── .gitignore               # config.yaml, __pycache__, .env
└── docs/
    └── plans/
        └── 2026-03-20-kodi-rating-backfill-design.md
```

## Key Design Decisions

- **Single file script** — no package structure, easy to run on a server
- **YAML config** — human-readable, supports complex mapping structure
- **Dry run by default** — safe to test without writing to DB
- **Rate limiting** — respect API limits (TMDB: 40 req/10s, OMDB: 1000/day)
- **Idempotent** — only touches movies with empty c12, safe to re-run
- **IMDB ID for OMDB** — much more reliable than title+year matching
- **Conservative inference** — rounds up to stricter rating when mapping
- **Configurable everything** — target country, prefix, mappings, DB connection — works for any Kodi setup, not just NL

## Test DB

- Host: docker.home.lan:3306, user: kodi, database: testvideo131
- 699 movies, 53 missing ratings
- All missing movies have both tmdb_id and imdb_id in uniqueid table
- Current prefix: "Rated "

## Out of Scope (for now)

- Kijkwijzer.nl scraping (can be added as a module later)
- TV show ratings (different table structure)
- Automatic re-scraping on schedule
- GUI / web interface
