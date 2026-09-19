# Repository Guidelines

## Project Structure & Module Organization
`main.py` is the entry point. Core workflow code lives in `src/`: `download_data.py` handles Selenium-driven exports, `process_data.py` parses CSVs, and `upload_data.py` is reserved for database writes. Shared helpers live in `utils/` (`web_scraper.py`, `data_cleaning.py`, `db_helper.py`). Configuration is split between `config/settings.json` for paths and URLs and `config/constants.py` for environment-backed constants. Runtime files land under `data/` (`downloads/`, `processed/`, `raw/`) and logs are written to `.log`.

## Build, Test, and Development Commands
Create and activate a virtual environment before installing dependencies:

```powershell
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
```

Run the current workflow with:

```powershell
python main.py
```

Use `python -m compileall src utils config` for a quick syntax check before opening a PR. There is no separate build step.

## Coding Style & Naming Conventions
Follow the existing Python style: 4-space indentation, `snake_case` for modules, functions, and variables, and `UPPER_CASE` for constants in `config/constants.py`. Keep helper functions small and focused; shared browser, parsing, or database utilities belong in `utils/`. Reuse `loguru` logging patterns already established in `main.py` instead of adding ad hoc `print()` debugging.

## Testing Guidelines
No automated test suite is committed yet. Validate changes with targeted manual runs against sample CSVs in `data/downloads` and confirm any processed output paths still match `config/settings.json`. If you add tests, place them under `tests/` and use `test_*.py` naming so they can be run with `python -m pytest`.

## Commit & Pull Request Guidelines
Keep commit messages short, imperative, and lowercase, matching recent history such as `fix settings.json path` or `add requirements.txt`. Pull requests should include a concise summary, linked issue or context when available, notes about `.env`, path, or database impact, and a short log snippet or screenshot only when it clarifies runtime behavior.

## Security & Configuration Tips
Do not commit `.env`, downloaded CSVs, or processed data. Required secrets include Energia XXI credentials and PostgreSQL connection variables referenced in `config/constants.py`. Treat `config/settings.json` as the source of truth for local folders; update it carefully if paths change.
