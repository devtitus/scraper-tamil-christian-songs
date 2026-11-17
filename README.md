# Tamil Christian Songs Lyrics Scraper

Configurable scraper for tamilchristiansongs.in that crawls album listing pages
and per-song lyrics pages, then writes two structured JSON files per album — one
for Tamil, one for Tanglish — with an identical document shape in both.

## Project status

| Stage | Status |
|-------|--------|
| venv + packages | done |
| Offline tests | 42 passed |
| Smoke test (3 songs) | pending |
| Full crawl (364 songs, ~62 min) | pending |

## Project layout

```
lyrics-scraper/
├── .venv\                          # virtualenv — every package lives here
├── config\
│   ├── settings.toml               # site URLs, crawl delay, schema rules, output layout
│   └── albums.toml                 # ★ configurable album list
├── requirements.txt
├── requirements-dev.txt
├── pyproject.toml
├── .gitignore
├── src\lyrics_scraper\             # package source
│   ├── __main__.py, cli.py
│   ├── config.py                   # TOML loading, frozen dataclasses
│   ├── logging_setup.py            # UTF-8 console + file logging
│   ├── http.py                     # throttled GET client, forced UTF-8 decode
│   ├── models.py                   # shared dataclasses
│   ├── parsers\album_page.py       # cards + pagination + totals
│   ├── parsers\lyrics_page.py      # tamil / tanglish / extras blocks
│   ├── transform.py                # ★ builds the parts/bridge/chorus/metadata/full_text schema
│   ├── pipeline.py                 # discover → fetch → checkpoint → write JSON
│   ├── output.py                   # atomic JSON writers + JSONL checkpoint
│   └── validate.py                 # structure validation
├── data\
│   ├── cache\                      # raw HTML cache (regenerable)
│   ├── output\                     # <-- per-album JSON deliverables live here
│   └── state\                      # JSONL checkpoints for resume
## Packages (all inside `.venv`)

| Package | Purpose |
|---------|---------|
| `requests` | HTTP client |
| `beautifulsoup4` | HTML parsing |
| `lxml` | fast parser backend (falls back to `html.parser`) |
| `tenacity` | retry on 429/5xx/network errors |
| `tqdm` | progress bars |
| `pytest` | test runner |

## Configuration

### `config/albums.toml` — which albums to scrape

Add as many `[[albums]]` blocks as you like. Only `enabled = true` albums are
scraped by default:

```toml
[[albums]]
slug    = "jebathotta-jeyageethangal"
enabled = true
max_pages = 0          # 0 = auto-detect from the site's pagination nav

[[albums]]
slug    = "christmas"
enabled = false
```

`max_pages` caps the crawl; leave it unset (or `0`) to scrape every page.

### `config/settings.toml` — crawl behaviour & schema rules

Key sections:
## The JSON schema

Every object in `tamil.json` and `tanglish.json` has **identical keys** in the
same order. Absent values are emitted as `null`, never omitted:

```json
{
  "parts": [
    {
      "title": "Part I",
      "stanzas": [{"lines": ["aapaththu Naalil Karththar", "en Jepam Kaetkinteer"], "number": 1}],
      "part_number": 1
    }
  ],
  "bridge": null,
  "chorus": null,
  "metadata": {
    "has_bridge": false,
    "has_chorus": false,
    "part_count": 1,
    "hymn_number": 1,
    "verse_count": 1,
    "stanza_format": "plain"
  },
  "full_text": "Hymn No. 1 1. aapaththu Naalil Karththar en Jepam Kaetkinteer"
}
```

`tamil.json[i]` and `tanglish.json[i]` are index-aligned and share
`metadata.hymn_number` (sequential 1…N), so the two files line up song-for-song.

By default `include_identity = false` keeps the strict schema (no title/slug/url
field). Flip `include_identity = true` in `config/settings.toml` to inject those
into `metadata`.

## CLI

```powershell
# Activate the venv once
.\.venv\Scripts\Activate.ps1

# List configured albums
python -m lyrics_scraper list-albums

# Smoke test: 1 page, 3 songs (~40 s with the 10s crawl-delay)
python -m lyrics_scraper scrape --album jebathotta-jeyageethangal --pages 1 --limit 3

# Full crawl: all 8 pages, all 364 songs (~62 min; resumed automatically if interrupted)
python -m lyrics_scraper scrape --album jebathotta-jeyageethangal

# Re-apply schema rules to cached data (no re-crawling — seconds, not minutes)
python -m lyrics_scraper transform --album jebathotta-jeyageethangal

# Validate the emitted JSON
python -m lyrics_scraper validate --album jebathotta-jeyageethangal
```

Output lands in `data/output/<album-slug>/`:
```
tamil.json          # 364 song documents (Tamil)
tanglish.json       # 364 song documents (Tanglish, index-aligned)
_scrape_report.json # counts, failures, cache stats, schema options
```

## Important site behaviours handled

1. **No charset in `Content-Type`.** The server sends bare `text/html`. The
   client always decodes `response.content` as UTF-8, so Tamil text does not
   turn into mojibake.
2. **Pagination wraps.** `?page=9` and `?page=99` return HTTP 200 with page-1
   content instead of 404. The page count is read from the
   `nav[aria-label="Pagination"]` links; probing until an empty page loops
   forever.
3. **`robots.txt` crawl-delay.** The site mandates `Crawl-delay: 10`. A full
   album crawl is ~372 requests ≈ 62 minutes. The scraper checkpoints after
   every song, so an interrupted run resumes exactly where it left off.
4. **Optional sections.** `#translated-content` (English meaning) is absent on
   some songs; those songs get `null` values, not missing keys.

## Running tests

```powershell
.\.venv\Scripts\python.exe -m pytest tests/
```

All tests are offline (fixture HTML included) and complete in under a second.


* `[scraping]` — `delay_seconds` (respects `robots.txt` `Crawl-delay: 10`),
  retries, timeouts.
* `[schema]` — chorus/bridge markers, whether `chorus` can be promoted by a
  repeat heuristic (default **off**), `full_text` prefix, and `include_identity`.
* `[output]` — where `tamil.json`, `tanglish.json`, and `_scrape_report.json`
  go.

Everything is overridable on the CLI (`--delay`, `--no-cache`, `--parser`, ...).

├── logs\scraper.log
└── tests\                          # offline pytest suite (fixtures included)
```
