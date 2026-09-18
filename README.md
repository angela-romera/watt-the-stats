# watt-the-stats

Download energy-consumption files from a website that does not expose an API.

The project currently covers **phase 1: web scraping and file downloads**. Parsing,
validation, and database insertion will be implemented in phase 2.

## Setup

```powershell
# Install uv once if it is not already available
powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"

# Create the environment from the reproducible lockfile
uv sync
```

Keep credentials in `.env`; never commit that file. A safe template is provided in
`.env.example`.

## Run

```powershell
uv run python main.py
```

The downloader signs in with the credentials from `.env`, discovers all contracts,
and downloads up to six available consumption periods for each one. Finished CSVs
are saved under `data/downloads` with contract and date-range filenames.

Scraper behavior such as headless mode, timeouts, and period count is configured in
`config/settings.json`.

## Project layout

```text
config/settings.json   Non-secret scraper settings
data/downloads/        Files downloaded by Selenium
data/processed/        Reserved for phase 2 output
src/config.py          Settings and environment loading
src/scraper.py         Login, browser setup, and CSV download workflow
main.py                Application entry point
```

## Quick validation

```powershell
uv run pytest
uv run ruff check .
uv run ruff format --check .
```
