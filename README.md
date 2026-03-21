<p align="center">
  <img src="assets/kijkwijzer-logo.svg" alt="Kijkwijzer" width="200">
</p>

# Kodi Rating Backfill

<p align="center">
  <img src="assets/al.svg" alt="AL" width="40">
  <img src="assets/6.svg" alt="6" width="40">
  <img src="assets/9.svg" alt="9" width="40">
  <img src="assets/12.svg" alt="12" width="40">
  <img src="assets/14.svg" alt="14" width="40">
  <img src="assets/16.svg" alt="16" width="40">
  <img src="assets/18.svg" alt="18" width="40">
</p>

Backfill missing age ratings in your Kodi library using TMDB, OMDB, and kijkwijzer.nl.

Kodi's TMDB scraper only fetches age ratings for one configured country. When that country's certification is missing on TMDB, the movie gets no rating at all. This tool fills those gaps by checking multiple sources and inferring ratings from other countries.

## How it works

The tool connects to Kodi via JSON-RPC and processes movies with empty ratings through a six-tier lookup:

0. **Manual overrides** — user-defined ratings in `overrides.yaml`
1. **TMDB direct** — target country certification (e.g. NL)
2. **TMDB inferred** — map from culturally similar countries (BE → DE → AT → FR → GB → DK → SE → US)
3. **OMDB** — US MPAA rating mapped to target scale
4. **Kijkwijzer.nl** — scrape the Dutch rating authority's website
5. **Fallback** — configurable default (e.g. `NR`) after a retry window expires

### Retry tracking

New movies that no source can resolve are not immediately given a fallback rating. Instead, they are tracked in `unresolved.json` with the date they were first seen. On each subsequent run, all sources are tried again. Only after `retry_days` (default: 30) have passed without a result is the fallback rating applied. This gives time for new movies to get certifications added to TMDB.

## Quick start

```bash
# Install dependencies
pip install -r requirements.txt

# Copy and edit config
cp config.example.yaml config.yaml
# Edit config.yaml: set your Kodi URL, TMDB key, and OMDB key

# Dry run (see what would change, no writes)
python3 backfill.py

# Apply changes
python3 backfill.py --no-dry-run

# Verbose output with log file
python3 backfill.py -v -l backfill.log
```

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

The `prefix` should match what your Kodi TMDB scraper uses. Check an existing rated movie in your library to see the format (e.g. `"Rated 12"` means prefix is `"Rated "`).

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
  overrides_file: "overrides.yaml"
  unresolved_file: "unresolved.json"
  # log_file: "backfill.log"
```

### Manual overrides

For movies that no source can resolve, create `overrides.yaml`:

```yaml
overrides:
  "Sprookjesboom de Musical - Een gi-ga-gantisch avontuur!": "AL"
  "Woezel En Pip - Alles Is Fijn Familiemusical": "AL"
```

Titles must match exactly as they appear in Kodi. Overrides are checked first (Tier 0) and take priority over all other sources.

See `overrides.example.yaml` for a template.

## CLI options

```
python3 backfill.py [-c CONFIG] [--dry-run | --no-dry-run] [-v] [-l LOGFILE]

  -c, --config    Path to config file (default: config.yaml)
  --dry-run       Log changes without writing to Kodi
  --no-dry-run    Actually write changes to Kodi
  -v, --verbose   Debug logging
  -l, --log-file  Write log output to file (in addition to console)
```

## Example output

```
08:14:07 INFO  Found 44 movies missing ratings (dry_run=True)
08:14:08 INFO  [DRY-RUN] Baas In Eigen Bos 2                -> Rated AL (source: tmdb-inferred-DE)
08:14:09 INFO  [DRY-RUN] Fast Charlie                        -> Rated 16 (source: tmdb-inferred-DE)
08:14:14 INFO  [PENDING] Ernst, Bobbie: Herrie op de Noordpool -> waiting for rating (since 2026-03-21)
08:14:35 INFO  [DRY-RUN] Reality                             -> Rated AL (source: tmdb-NL)
08:14:38 INFO  Kijkwijzer: matched 'Sprookjesboom de Musical' -> https://www.kijkwijzer.nl/films/sprookjesboom/
08:14:38 INFO  [DRY-RUN] Sprookjesboom de Musical            -> Rated AL (source: kijkwijzer)
08:14:57 INFO  Tracking 9 unresolved movies in unresolved.json
08:14:57 INFO  --- Summary ---
08:14:57 INFO  Overrides:     0
08:14:57 INFO  TMDB direct:   5
08:14:57 INFO  TMDB inferred: 24
08:14:57 INFO  OMDB:          0
08:14:57 INFO  Kijkwijzer:    6
08:14:57 INFO  Fallback:      0
08:14:57 INFO  Pending:       9
08:14:57 INFO  Errors:        0
```

### Log labels

| Label | Meaning |
|-------|---------|
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

## Running on a schedule

Add to cron to run daily:

```bash
# Run daily at 3am, log to file
0 3 * * * cd /path/to/kodi-rating-backfill && python3 backfill.py --no-dry-run -l backfill.log
```

## Files

| File | Tracked | Purpose |
|------|---------|---------|
| `backfill.py` | Yes | Main script |
| `config.example.yaml` | Yes | Config template |
| `overrides.example.yaml` | Yes | Overrides template |
| `config.yaml` | No | Your config (contains API keys) |
| `overrides.yaml` | No | Your manual overrides |
| `unresolved.json` | No | Retry tracker (auto-managed) |

## License

MIT
