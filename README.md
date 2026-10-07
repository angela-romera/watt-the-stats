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

Invoice report emails use the `SMTP_HOST`, `SMTP_PORT`, `SMTP_USERNAME`, and
`SMTP_APP_PASSWORD` variables. For Gmail, set `SMTP_APP_PASSWORD` to a Gmail
app password (not the account's usual password). The recipient is read from
`client.email_to`.

Report emails contain a centered consumption pie, a podium of the three
highest-consumption days, and columns by price band. Where complete previous-year
data exists, the columns compare the two periods. The charts load through
QuickChart HTTPS URLs, following an HTML-only email pattern. The email has
only plain-text and HTML parts, so
it has no PNG attachments. Its images scale to the email width. Recipients may
need to enable remote images in their email app.

QuickChart receives the aggregate consumption amounts and the dates shown in
the podium because these values are encoded in each image URL. A person with a
complete URL can see that chart. No recipient address, invoice ID, credentials,
or raw hourly data are included. The separate chart service remains private and
is not used by report emails. Making it private prevents Gmail from loading its
images directly.

Each send also renders the same report data as three Matplotlib PNGs and keeps
them locally under `report_chart_dir` from `config/settings.json`. These files
are ignored by Git and are never attached to the email. To inspect the chart
design with fictitious data without a database connection or email:

```powershell
uv run python -m src.preview_charts
```

Open `data/processed/chart-preview/index.html` for the three chart designs and
examples with zero consumption, small values, ties, and missing history.

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
All accounts share the configured download folder; numbered filename
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

To test the email connection without downloading, processing, or uploading data,
send an empty report for the most recently inserted invoice of `client_id=1`:

```powershell
uv run python main.py --send-empty-report
```

This command requires the PostgreSQL settings, all four `SMTP_*` variables, and a
non-empty `client.email_to` value for client 1.

To send the current report version, containing the latest invoice's period,
tariff, total consumption in kWh, and total cost in EUR, run:

```powershell
uv run python main.py --send-report
```
After a confirmed upload (including an existing duplicate), the matching staging
JSON and source CSV are deleted after the database transaction completes. Failed or
uncertain uploads retain staging JSON for inspection. A cleanup error is logged
and recorded in the import report without changing the committed database outcome.
Processing-only mode retains its JSON output; `import_report.json` is kept.

Upload mode commits each CSV separately, then handles the original CSV according to
its outcome:

- Imported: the transaction inserted an invoice or missing hourly details; delete the CSV.
- Duplicate: every row already exists; delete the CSV after database verification.
- `data/failed`: invalid CSV data, missing/ambiguous clients, or conflicting stored
  values prevented the file from importing. That file's transaction is rolled back;
  other files continue.

The failed folder is configured in `config/settings.json`. If a failed archive
filename already exists, a numbered suffix preserves both files. The legacy
`imported_dir` and `duplicates_dir` settings are unused; existing archives are untouched.
Files are deleted or moved only after a confirmed outcome. Connection errors stop the run and leave
the current and remaining files in downloads; a lost connection during commit can
have an unknown outcome, so the next run checks the database again. CSV deletion or archive failures
also leave the source in downloads even if its database transaction already committed.
`data/processed/import_report.json` records filenames, results, deletion confirmation, errors, and totals
for the latest upload run. A run with failures or pending files exits with status 1.
The report is informational; duplicate detection compares parsed CSV data with
database records and does not depend on filenames or previous reports.
To retry a failed file after fixing the problem, move it back into `data/downloads`.
Processing-only mode does not move or delete CSVs. `.xls` files are not imported or deleted.

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
src/import_files.py    Per-file upload outcomes, successful CSV deletion, and failed CSV archiving
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
