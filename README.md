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

The website credentials are `USER_ENERGIAXXI_1` and `PWD_ENERGIAXXI_1`. PostgreSQL
uses `SERVER_WTS`, `DB_WTS_ELEC`, `USER_DB_WTS`, `PWD_DB_WTS`, and `PORT_DB_WTS`.
`PORT_DB_WTS` defaults to `5432`; `SCHEMA_DB_WTS` defaults to `public`; and
`SSLMODE_DB_WTS` defaults to `prefer`. `USER_ENERGIAXXI_1` identifies the existing
`login.username` whose client is matched by CUPS.

Initialize a new database with [sql/postgresql_schema.sql](sql/postgresql_schema.sql).
The script targets PostgreSQL's `public` schema. It is for a new database and
does not migrate an existing SQL Server schema. The uploader manages neither
`login.username` nor `client`; both must already exist.

## Run

The default command processes CSVs already present in `data/downloads`, writes
validated JSON to `data/processed`, and inserts invoice and detail data into PostgreSQL:

```powershell
uv run python main.py
```

`--upload` is an explicit alias for the default mode. To validate CSVs and write
staging JSON without connecting to PostgreSQL, use:

```powershell
uv run python main.py --process-only
```

The browser download code is commented out in `main.py`; runs use existing CSVs.
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

The uploader resolves an existing client using `USER_ENERGIAXXI_1` and CUPS. It
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
