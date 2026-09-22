# WSBtool

WSBtool is a lightweight scraper and publishing project for the Rainbowlers League dashboard hosted on League Secretary. It is designed to:

- pull public league report links from the dashboard
- save raw HTML snapshots for debugging and historical review
- index report metadata such as season, week, title, and URL
- provide a simple foundation for publishing the latest data to a public website or GitHub Pages site

This project is intentionally conservative and beginner-friendly: it focuses on public HTML, stores data locally, and keeps a clear record of what was fetched.

---

## Why this exists

The league dashboard contains public reports such as standings, recaps, statistics, schedule pages, and other league documents. This project helps automate the process of:

- discovering those report URLs
- downloading them on a schedule
- storing them as raw HTML for inspection
- keeping a simple record of what changed week to week

This is useful for:
- weekly reporting
- a small static website
- internal dashboards
- future data extraction and analysis

---

## Project goals

- Scrape only public pages
- Maintain a stable data index
- Save raw HTML to help with debugging
- Minimize complexity for a novice engineer
- Support future expansion into parsed tables and charts

---

## Tech stack

This project currently uses:

- Python 3
- requests
- BeautifulSoup
- GitHub Actions for scheduled jobs
- GitHub Pages for published output

You can later expand this into a more advanced site using:
- Flask
- FastAPI
- Django
- Next.js
- React
- Jinja templates

---

## Repository structure

```text
WSBtool/
├── .github/
│   └── workflows/
│       ├── pages.yml
│       └── update.yml
├── data/
│   ├── raw/
│   └── index.json
├── scraper/
│   ├── __init__.py
│   ├── cli.py
│   ├── main.py
│   ├── models.py
│   └── source.py
├── site/
├── README.md
├── requirements.txt
├── site_generator.py
├── .gitignore
└── test/
    └── test_source.py
```

---

## Installation

Clone the repository:

```bash
git clone https://github.com/grantcoreya/WSBtool.git
cd WSBtool
```

Create a virtual environment:

```bash
python3 -m venv .venv
source .venv/bin/activate
```

Install dependencies:

```bash
pip install -r requirements.txt
```

---

## Running the scraper

From the project root:

```bash
python -m scraper.cli --output data
```

Optional flags:

```bash
python -m scraper.cli --output data --backfill
python -m scraper.cli --output data --verbose
```

What happens:

- the scraper visits the public League Secretary dashboard
- finds likely report links
- filters to supported seasons
- downloads the HTML for relevant report pages
- stores each page in `data/raw/`
- writes a metadata index to `data/index.json`

---

## Output format

The scraper stores a JSON index at:

```text
data/index.json
```

Each record contains fields similar to:

```json
{
  "url": "https://www.leaguesecretary.com/...",
  "title": "Statistics",
  "season": "2026-2027",
  "week": "Week 2",
  "kind": "statistics",
  "sha256": "abc123...",
  "raw_file": "raw/abcd1234.html",
  "fetched_at": "2026-09-22T00:00:00+00:00",
  "source": "League Secretary"
}
```

This data can be used to:
- trigger a rebuild
- compare weekly changes
- generate a website page
- archive historical snapshots

---

## How the scraper works

The scraper is intentionally simple and readable:

1. Request the public dashboard page
2. Parse all visible links from the HTML
3. Filter for likely report URLs using keywords like:
   - standings
   - stats
   - recap
   - results
   - documents
   - schedule
4. Keep only supported season-like values
5. Save the HTML for each selected report
6. Store a hash to avoid re-downloading unchanged files

This keeps the pipeline easy to debug for a new engineer.

---

## Scheduling

You can run the scraper on a schedule using GitHub Actions, cron, or a hosted server.

Example schedule:

- Every Friday at 8:00 AM
- Every Saturday at 6:00 AM
- After a new league week is posted

A common workflow is:

- run scraper
- save updates
- generate static web pages
- publish to GitHub Pages

---

## Publishing to a website

This project is designed to support a static site output. A typical pattern is:

- scrape latest HTML
- parse selected content into structured data
- generate HTML pages
- upload to a static host

For GitHub Pages, the generated site can be placed in a `site/` folder and published automatically.

---

## Development notes

### Good debugging habits
- Always check the raw HTML first when a scraper fails
- Inspect `data/raw/` before changing parsing logic
- Compare `index.json` to detect missing or duplicate entries
- Use `--verbose` mode for more details

### When the site changes
League Secretary may change:
- page structure
- URL patterns
- season labels
- week selectors
- downloadable report links

If this happens, update:
- `REPORT_KEYWORDS`
- `SEASON_PATTERNS`
- `WEEK_PATTERNS`
- the report detection logic in `scraper/source.py`

---

## Troubleshooting

### The scraper returns no reports
Check:
- whether the site is reachable
- whether the page requires JavaScript or dynamic render logic
- whether the report links use different text than expected

### The scraper skips everything
Check:
- whether the season filter is too restrictive
- whether `index.json` already contains the same content hash
- whether the site is returning different pages than expected

### HTML looks different than expected
Open the saved file in `data/raw/` and inspect:
- anchor tags
- report names
- season/week labels
- embedded PDF URLs or document links

---

## Future enhancements

Potential next steps for this project:

- parse standings tables into structured JSON
- extract weekly score summaries
- detect PDF report links and download them
- generate charts for standings and team averages
- create a more polished front-end site
- add automated tests for scraper logic

---

## Contribution

This project is intended to be easy to understand and modify. Contributions are welcome, especially improvements to:

- more accurate report detection
- better season/week parsing
- cleaner static site generation
- handling new League Secretary page layouts

---

## License

This project is currently intended for local development and internal usage unless otherwise specified. Add a license file if you intend to publish it publicly.

---

## Example commands

```bash
# Run the scraper once
python -m scraper.cli --output data

# Run a full backfill
python -m scraper.cli --output data --backfill

# Run with extra logging
python -m scraper.cli --output data --verbose
```

---

## Summary

WSBtool provides a simple and extensible starting point for collecting public league data from League Secretary and turning it into a website or reporting app. It is intentionally designed to be easy for a novice engineer to read, debug, and extend.

If you want, I can also generate:
- a more polished README aimed at GitHub visitors
- a shorter project README for a starter repo
- a README tailored for a production static site deployment
- or a complete `requirements.txt` and GitHub Actions workflow set
