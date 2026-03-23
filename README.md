<p align="center">
  <img src="assets/kijkwijzer-logo.svg" alt="Kijkwijzer" width="400">
</p>

# Kodi Kijkwijzer

<p align="center">
  <img src="assets/al.svg" alt="AL" width="40">
  <img src="assets/6.svg" alt="6" width="40">
  <img src="assets/9.svg" alt="9" width="40">
  <img src="assets/12.svg" alt="12" width="40">
  <img src="assets/14.svg" alt="14" width="40">
  <img src="assets/16.svg" alt="16" width="40">
  <img src="assets/18.svg" alt="18" width="40">
</p>

Backfill missing age ratings for **movies and TV shows** in your Kodi library using TMDB, OMDB, and kijkwijzer.nl.

Kodi's TMDB scraper only fetches age ratings for one configured country. When that country's certification is missing on TMDB, the movie or TV show gets no rating at all. This tool fills those gaps by checking multiple sources and inferring ratings from other countries.

## How it works

The tool connects to Kodi via JSON-RPC and processes movies and TV shows with empty ratings through a six-tier lookup:

0. **Manual overrides** — user-defined ratings in `overrides_movies.yaml` / `overrides_tvshows.yaml`
1. **TMDB direct** — target country certification (e.g. NL)
2. **TMDB inferred** — map from culturally similar countries (BE → DE → AT → FR → GB → DK → SE → US)
3. **OMDB** — US MPAA rating mapped to target scale
4. **Kijkwijzer.nl** — scrape the Dutch rating authority's website
5. **Fallback** — configurable default (e.g. `NR`) after a retry window expires

Movies use TMDB's `/movie/{id}/release_dates` endpoint. TV shows use `/tv/{id}/content_ratings`. Both are handled automatically.

### Retry tracking

Items that no source can resolve are not immediately given a fallback rating. Instead, they are tracked with the date they were first seen. On each subsequent run, all sources are tried again. Only after `retry_days` (default: 30) have passed without a result is the fallback rating applied. This gives time for new releases to get certifications added to TMDB.

Tracker files are separate per media type: `unresolved_movies.json` and `unresolved_tvshows.json`.

## Quick start

```bash
# Install dependencies
pip install -r requirements.txt

# Copy and edit config
cp config.example.yaml config.yaml
# Edit config.yaml: set your Kodi URL, TMDB key, and OMDB key

# Dry run (see what would change, no writes)
python -m kodi_kijkwijzer

# Apply changes
python -m kodi_kijkwijzer --no-dry-run

# Movies only / TV shows only
python -m kodi_kijkwijzer --movies-only
python -m kodi_kijkwijzer --tvshows-only

# Verbose output with log file
python -m kodi_kijkwijzer -v -l backfill.log
```

The legacy `python backfill.py` entry point also still works.

## Requirements

- Python 3.8+
- Kodi with JSON-RPC enabled (Settings > Services > Control > Allow remote control via HTTP)
- [TMDB API key](https://www.themoviedb.org/settings/api) (free)
- [OMDB API key](https://www.omdbapi.com/apikey.aspx) (free tier: 1,000 requests/day)

## Configuration

### Kodi connection

```yaml
kodi:
  url: "http://localhost:8080/jsonrpc"
  # username: ""   # if Kodi requires authentication
  # password: ""
```

Uses Kodi's JSON-RPC API — enable the web server in Kodi settings under **Services > Control**.

### API keys

```yaml
tmdb:
  api_key: ""    # get one at https://www.themoviedb.org/settings/api

omdb:
  api_key: ""    # get one at https://www.omdbapi.com/apikey.aspx
```

### Rating settings

```yaml
rating:
  prefix: "Rated "           # prepended to rating value (match your scraper config)
  target_country: "NL"       # ISO 3166-1 country code
  fallback_rating: "NR"      # applied after retry window expires (empty string to skip)
  inference_countries: [...]  # priority order for cross-country inference
  mappings: {...}             # country-specific rating → target scale mapping
```

The `prefix` should match what your Kodi TMDB scraper uses. Check an existing rated item in your library to see the format (e.g. `"Rated 12"` means prefix is `"Rated "`).

### Inference

When the target country's rating is missing on TMDB, the tool checks other countries in priority order and maps their rating to the target scale. Countries are ordered by cultural similarity:

| Priority | Country | System | Rationale |
|----------|---------|--------|-----------|
| 1 | BE | Kijkwijzer | Same system as NL |
| 2 | DE | FSK | Very similar thresholds |
| 3 | AT | ABMC | Similar to DE |
| 4 | FR | CNC | Geographically close |
| 5 | GB | BBFC | Good middle ground |
| 6 | DK | Medierådet | Nordic neighbor |
| 7 | SE | Swedish Media Council | Nordic, similar values |
| 8 | US | MPAA | Last resort (different philosophy) |

US is last because its rating philosophy differs from European norms — conservative on nudity/sex but lenient on violence.

All mappings are configurable in `config.yaml`. The default mappings use conservative rounding (always maps to the stricter bracket).

**Note:** YAML mapping keys must be quoted strings (e.g. `"6": "6"`, not `6: "6"`). Unquoted numbers are parsed as integers and will silently fail to match. The tool normalizes keys automatically, but quoting them in config avoids confusion.

### Options

```yaml
options:
  dry_run: true              # log changes without writing (default: true)
  rate_limit: 0.25           # seconds between API calls
  retry_days: 30             # days to retry before applying fallback
  kijkwijzer: true           # enable kijkwijzer.nl scraping
  # log_file: "backfill.log"
```

### Manual overrides

Override files are separate per media type. Create them as needed:

**`overrides_movies.yaml`** — movie overrides:
```yaml
overrides:
  "Sprookjesboom de Musical - Een gi-ga-gantisch avontuur!": "AL"
  "Woezel En Pip - Alles Is Fijn Familiemusical": "AL"
```

**`overrides_tvshows.yaml`** — TV show overrides:
```yaml
overrides:
  "The Pirate Bay": "16"
```

Titles must match exactly as they appear in Kodi. Overrides are checked first (Tier 0) and take priority over all other sources.

See `overrides_movies.example.yaml` and `overrides_tvshows.example.yaml` for templates.

## CLI options

```
python -m kodi_kijkwijzer [-c CONFIG] [--movies-only | --tvshows-only]
                          [--dry-run | --no-dry-run] [-v] [-l LOGFILE]

  -c, --config       Path to config file (default: config.yaml)
  --movies-only      Only process movies
  --tvshows-only     Only process TV shows
  --dry-run          Log changes without writing to Kodi
  --no-dry-run       Actually write changes to Kodi
  -v, --verbose      Debug logging
  -l, --log-file     Write log output to file (in addition to console)
```

## Example output

```
11:16:59 INFO  === Movies: found 44 missing ratings (dry_run=True) ===
11:16:59 INFO  [Movie] [DRY-RUN] Baas In Eigen Bos 2                -> Rated AL (source: tmdb-inferred-DE)
11:17:01 INFO  [Movie] [DRY-RUN] Fast Charlie                        -> Rated 16 (source: tmdb-inferred-DE)
11:17:04 INFO  [Movie] [PENDING] Ernst, Bobbie: Herrie op de Noordpool -> waiting for rating (since 2026-03-21)
11:17:35 INFO  [Movie] [DRY-RUN] Reality                             -> Rated AL (source: tmdb-NL)
11:17:38 INFO  [Movie] [DRY-RUN] Sprookjesboom de Musical            -> Rated AL (source: kijkwijzer)
11:17:48 INFO  --- Movie Summary ---
11:17:48 INFO  TMDB direct:   5
11:17:48 INFO  TMDB inferred: 24
11:17:48 INFO  Kijkwijzer:    6
11:17:48 INFO  Pending:       9

11:17:48 INFO  === TV Shows: found 27 missing ratings (dry_run=True) ===
11:18:04 INFO  [TV Show] [DRY-RUN] The Serial Killer's Wife          -> Rated 14 (source: tmdb-NL)
11:18:06 INFO  [TV Show] [DRY-RUN] The Undeclared War                -> Rated 9 (source: kijkwijzer)
11:18:15 INFO  --- TV Show Summary ---
11:18:15 INFO  TMDB direct:   9
11:18:15 INFO  TMDB inferred: 7
11:18:15 INFO  Kijkwijzer:    9
11:18:15 INFO  Pending:       2
```

### Log labels

| Label | Meaning |
|-------|---------|
| `[Movie]` / `[TV Show]` | Media type being processed |
| `[DRY-RUN]` | Would set this rating (dry run mode) |
| `[UPDATE]` | Rating was written to Kodi |
| `[PENDING]` | No rating found yet, still in retry window |

### Source labels

| Source | Meaning |
|--------|---------|
| `tmdb-NL` | Direct match from TMDB for target country |
| `tmdb-inferred-DE` | Mapped from German FSK rating |
| `omdb` | Mapped from US MPAA rating via OMDB |
| `kijkwijzer` | Scraped from kijkwijzer.nl |
| `override` | Manual override from overrides.yaml |
| `fallback` | Retry window expired, applied fallback rating |

## Architecture

```
kodi_kijkwijzer/
├── __init__.py          # version
├── __main__.py          # python -m entry point
├── cli.py               # argparse, logging setup
├── config.py            # config loading, validation, overrides
├── kodi.py              # Kodi JSON-RPC (generic, uses MediaType)
├── providers/
│   ├── tmdb.py          # TMDB lookup (movie + TV via MediaType)
│   ├── omdb.py          # OMDB fallback
│   └── kijkwijzer.py    # kijkwijzer.nl scraper
├── tracker.py           # retry tracker (unresolved items)
├── backfill.py          # orchestration loop
└── media_types.py       # MOVIE + TVSHOW definitions
```

The `MediaType` dataclass captures all differences between movies and TV shows (JSON-RPC methods, TMDB endpoints, response parsers). All other code is generic.

## Running on a schedule

Add to cron to run daily:

```bash
# Run daily at 3am, log to file
0 3 * * * cd /path/to/kodi-kijkwijzer && python3 -m kodi_kijkwijzer --no-dry-run -l backfill.log
```

## Upgrading from v1

- `python backfill.py` still works (backwards-compatible wrapper)
- `unresolved.json` is automatically migrated to `unresolved_movies.json` on first run
- Legacy `overrides.yaml` should be renamed to `overrides_movies.yaml`

## Files

| File | Tracked | Purpose |
|------|---------|---------|
| `kodi_kijkwijzer/` | Yes | Main package |
| `backfill.py` | Yes | Backwards-compatible wrapper |
| `config.example.yaml` | Yes | Config template |
| `overrides_movies.example.yaml` | Yes | Movie overrides template |
| `overrides_tvshows.example.yaml` | Yes | TV show overrides template |
| `config.yaml` | No | Your config (contains API keys) |
| `overrides_movies.yaml` | No | Movie rating overrides |
| `overrides_tvshows.yaml` | No | TV show rating overrides |
| `unresolved_movies.json` | No | Movie retry tracker (auto-managed) |
| `unresolved_tvshows.json` | No | TV show retry tracker (auto-managed) |

## License

MIT
