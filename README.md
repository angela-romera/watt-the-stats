# watt-the-stats

Transform energy-consumption CSVs into validated invoice and hourly-detail data,
and insert that data into PostgreSQL by default.

## Setup

```powershell
# Install uv once if it is not already available
powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"

# Create the environment from the reproducible lockfile
uv sync
```

Keep credentials in `.env`; never commit that file. A template is provided in
`.env.example`.

The website credentials are two JSON lists in `USER_ENERGIAXXI_LIST` and
`PWD_ENERGIAXXI_LIST`, paired by position:

```dotenv
USER_ENERGIAXXI_LIST='["first-user", "second-user"]'
PWD_ENERGIAXXI_LIST='["first-password", "second-password"]'
```

Both lists must have the same length and contain non-empty strings. For one
account, use one-element lists such as `'["my-user"]'` and `'["my-password"]'`;
replace existing scalar values with this format. Use JSON escaping inside each
list for double quotes (`\"`) and backslashes (`\\`). Password whitespace is
preserved. Missing or empty lists are allowed for `--process-only`; downloads
require at least one credential pair. Invalid list formats or unequal lengths
are rejected before opening a browser. Credentials are never stored in settings.json.

PostgreSQL
uses `SERVER_WTS`, `DB_WTS_ELEC`, `USER_DB_WTS`, `PWD_DB_WTS`, and `PORT_DB_WTS`.
`PORT_DB_WTS` defaults to `5432`; `SCHEMA_DB_WTS` defaults to `public`; and
`SSLMODE_DB_WTS` defaults to `prefer`. Each entry in `USER_ENERGIAXXI_LIST` identifies
an existing `login.username` whose client can be matched by CUPS.

Initialize a new database with [sql/postgresql_schema.sql](sql/postgresql_schema.sql).
The script targets PostgreSQL's `public` schema. It is for a new database and
does not migrate an existing SQL Server schema. The uploader manages neither
`login.username` nor `client`; both must already exist.

## Run

The default command downloads CSVs from Energia XXI into `data/downloads`, processes
all CSVs in that folder, writes validated JSON to `data/processed`, and inserts
invoice and detail data into PostgreSQL:

```powershell
uv run python main.py
```

Accounts are downloaded sequentially, each in a fresh browser session. Every
account's contracts use `periods_to_download` from `config/settings.json`.
All accounts share the configured download and archive folders; numbered filename
suffixes preserve exports with identical names. A download error stops the run;
files already downloaded remain available for the next run.

`--upload` is an explicit alias for the default mode. To validate CSVs and write
staging JSON without connecting to PostgreSQL, use:

```powershell
uv run python main.py --process-only
```

`--process-only` skips browser downloads and uses existing CSVs.
Both folders are configured in `config/settings.json`; reprocessing replaces the
matching staging JSON files.
After a confirmed upload (including an existing duplicate), the matching staging
JSON is deleted because subsequent imports use the CSV and database. Failed or
uncertain uploads retain staging JSON for inspection. A cleanup error is logged
and recorded in the import report without changing the committed database outcome.
Processing-only mode retains its JSON output; `import_report.json` is kept.

Upload mode commits each CSV separately, then moves the original CSV according to
its outcome. The folders are configured in `config/settings.json`:

- `data/imported`: the transaction inserted an invoice or missing hourly details.
- `data/duplicates`: every row already exists; no insert was needed.
- `data/failed`: invalid CSV data, missing/ambiguous clients, or conflicting stored
  values prevented the file from importing. That file's transaction is rolled back;
  other files continue.

If an archive filename already exists, a numbered suffix preserves both files.
Files move only after a confirmed outcome. Connection errors stop the run and leave
the current and remaining files in downloads; a lost connection during commit can
have an unknown outcome, so the next run checks the database again. Archive failures
also leave the source in downloads even if its database transaction already committed.
`data/processed/import_report.json` records filenames, results, errors, and totals
for the latest upload run. A run with failures or pending files exits with status 1.
The report is informational; duplicate detection compares parsed CSV data with
database records and does not depend on filenames or previous reports.
To retry a failed file after fixing the problem, move it back into `data/downloads`.
Processing-only mode does not move files. `.xls` files are not imported or moved.

## Transformation and PostgreSQL upload

Each CSV produces one invoice with `initial_date`, `final_date`, and `tariff` from
its metadata, plus its supplied hourly details. Consumption remains in **Wh**;
prices are normalized to **EUR/kWh**. Costs are preserved, including zero values,
and Decimal values are inserted without float conversion.

Numeric hours are interpreted as interval-end timestamps: `1.0` is `00:00` on the
same date and `24.0` is `23:00` on the previous date. Explicit `00:00-01:00`
ranges use their start. Invoice dates remain exactly as exported; missing hours
are not invented and repeated local hours are retained.

The uploader resolves an existing client using CUPS and any username in
`USER_ENERGIAXXI_LIST`, including for CSVs left over from earlier runs. Exactly one
client must match across the configured accounts; missing or ambiguous matches
fail without inserting data. Keep the relevant usernames configured when retrying
old files. It
identifies an invoice by the exact client, initial date, and final date. A tariff
conflict fails. Existing identical details are skipped; a strict subset is filled
with the missing details. Conflicting or extra stored details fail, and that file's
transaction rolls back. Repeated local hours are compared as multisets, preserving
multiplicity. Coarse invoice/detail table locks serialize concurrent uploads.

Invalid metadata, unsupported columns, dates outside the invoice period, mismatched
consumption totals, and decimals that overflow or lose precision fail validation
before database writes. `login.username` and `client` are user-managed and are
never inserted or modified by the uploader.

## Project layout

```text
config/settings.json   Non-secret scraper settings and runtime folders
data/downloads/        CSV files used by processing and upload
data/processed/        Invoice/detail staging JSON (ignored by Git)
sql/postgresql_schema.sql  PostgreSQL schema for a new database
src/config.py          Settings and environment loading
src/scraper.py          Login, browser setup, and CSV download workflow
src/process_data.py    CSV validation, transformation, and staging output
src/upload_data.py     PostgreSQL upload workflow
src/import_files.py    Per-file upload outcomes and CSV archiving
main.py               Application entry point
```

## Quick validation

```powershell
uv run pytest
uv run ruff check .
uv run ruff format --check .
```

PostgreSQL integration tests are skipped unless `TEST_POSTGRES_DSN` is set to a
disposable test database connection string. They create and remove isolated test
schemas and test repeat imports, partial data, conflicts, rollback, and concurrency.
The test database account needs permission to create schemas. Never point this
variable at a production database.

The uploader uses Psycopg's [transaction handling](https://www.psycopg.org/psycopg3/docs/basic/transactions.html)
and PostgreSQL [table locks](https://www.postgresql.org/docs/current/explicit-locking.html)
to keep the existence checks and writes together. The upload account needs SELECT
on all four tables, INSERT/UPDATE privileges for the invoice/detail table locks,
UPDATE privileges on client/login for row locks, and access to identity sequences.
Row locking does not modify client or login values. Uploads wait up to 30 seconds
for locks; failures roll back and can be retried.
