"""Opt-in integration tests; TEST_POSTGRES_DSN must point to a disposable database."""

import os
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import date, time
from decimal import Decimal
from pathlib import Path
from threading import Barrier
from uuid import uuid4

import psycopg
import pytest
from psycopg import sql
from psycopg.conninfo import conninfo_to_dict

from src.config import DatabaseSettings
from src.process_data import Detail, Invoice
from src.upload_data import UploadConflict, upload_invoices

DSN = os.getenv("TEST_POSTGRES_DSN")
pytestmark = pytest.mark.skipif(
    not DSN, reason="Set TEST_POSTGRES_DSN for a disposable PostgreSQL DB"
)


@pytest.fixture
def database():
    schema = "test_upload_" + uuid4().hex
    values = conninfo_to_dict(DSN)
    settings = DatabaseSettings(
        host=values.get("host", "localhost"),
        dbname=values["dbname"],
        user=values["user"],
        password=values.get("password", ""),
        port=int(values.get("port", 5432)),
        schema=schema,
        sslmode=values.get("sslmode", "prefer"),
    )
    with psycopg.connect(DSN, autocommit=True) as connection:
        connection.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(schema)))
        try:
            ddl = (Path(__file__).parents[1] / "sql/postgresql_schema.sql").read_text()
            connection.execute(ddl.replace("public.", f'"{schema}".'))
            connection.execute(sql.SQL("SET search_path TO {}").format(sql.Identifier(schema)))
            connection.execute("INSERT INTO login (username) VALUES ('test-user')")
            connection.execute("INSERT INTO client (cups, id_login) VALUES ('ES-TEST', 1)")
            yield settings, connection
        finally:
            connection.execute(sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(schema)))


@pytest.fixture
def invoice():
    day = date(2026, 1, 1)
    detail = Detail(
        day,
        time(0),
        Decimal("147.0"),
        Decimal("0.167440358000000"),
        Decimal("0.02461373262600000000"),
    )
    return Invoice("ES-TEST", day, day, "2.0TD", (detail, replace(detail, hour=time(1))))


def test_insert_repeat_and_preserve_user_managed_tables(database, invoice):
    settings, connection = database
    first = upload_invoices([invoice], settings, "test-user")
    second = upload_invoices([invoice], settings, "test-user")
    assert (first.invoices_inserted, first.details_inserted) == (1, 2)
    assert (second.invoices_inserted, second.details_inserted, second.invoices_skipped) == (0, 0, 1)
    assert connection.execute("SELECT count(*) FROM invoice").fetchone() == (1,)
    assert connection.execute("SELECT count(*) FROM detail").fetchone() == (2,)
    assert connection.execute("SELECT * FROM login").fetchall() == [(1, "test-user", None)]
    assert connection.execute("SELECT * FROM client").fetchall() == [(1, None, None, "ES-TEST", 1)]
    stored = connection.execute(
        "SELECT date, hour, consumption, price, cost_per_hour FROM detail ORDER BY hour"
    ).fetchall()
    assert stored == [detail.insert_values(1)[:-1] for detail in invoice.details]


def test_fill_partial_and_preserve_repeated_hours(database, invoice):
    settings, connection = database
    repeated = replace(invoice, details=(invoice.details[0],) * 2)
    upload_invoices([replace(repeated, details=repeated.details[:1])], settings, "test-user")
    result = upload_invoices([repeated], settings, "test-user")
    assert (result.invoices_inserted, result.details_inserted) == (0, 1)
    assert upload_invoices([repeated], settings, "test-user").details_inserted == 0
    assert connection.execute("SELECT count(*) FROM detail").fetchone() == (2,)


@pytest.mark.parametrize("conflict", ["tariff", "details"])
def test_conflict_rolls_back_whole_batch(database, invoice, conflict):
    settings, connection = database
    upload_invoices([invoice], settings, "test-user")
    changed = (
        replace(invoice, tariff="OTHER")
        if conflict == "tariff"
        else replace(invoice, details=(replace(invoice.details[0], consumption=Decimal("99.0")),))
    )
    new_day = date(2026, 2, 1)
    new = replace(
        invoice,
        initial_date=new_day,
        final_date=new_day,
        details=tuple(replace(d, date=new_day) for d in invoice.details),
    )
    with pytest.raises(UploadConflict):
        upload_invoices([new, changed], settings, "test-user")
    assert connection.execute("SELECT count(*) FROM invoice").fetchone() == (1,)
    assert connection.execute("SELECT count(*) FROM detail").fetchone() == (2,)


@pytest.mark.parametrize("username", ["unknown", "test-user"])
def test_missing_or_ambiguous_client_rejected(database, invoice, username):
    settings, connection = database
    if username == "test-user":
        connection.execute("INSERT INTO client (cups, id_login) VALUES ('ES-TEST', 1)")
    with pytest.raises(UploadConflict, match="exactly one"):
        upload_invoices([invoice], settings, username)
    assert connection.execute("SELECT count(*) FROM invoice").fetchone() == (0,)


def test_concurrent_uploads_do_not_duplicate(database, invoice):
    settings, connection = database
    barrier = Barrier(2)

    def upload():
        barrier.wait(timeout=10)
        return upload_invoices([invoice], settings, "test-user")

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(upload) for _ in range(2)]
        results = [future.result(timeout=40) for future in futures]
    assert sum(result.invoices_inserted for result in results) == 1
    assert sum(result.details_inserted for result in results) == 2
    assert connection.execute("SELECT count(*) FROM invoice").fetchone() == (1,)
    assert connection.execute("SELECT count(*) FROM detail").fetchone() == (2,)


def test_detail_insert_error_rolls_back_invoice(database, invoice):
    settings, connection = database
    invalid = replace(invoice, details=(replace(invoice.details[0], price=Decimal("100")),))
    with pytest.raises(psycopg.errors.NumericValueOutOfRange):
        upload_invoices([invalid], settings, "test-user")
    assert connection.execute("SELECT count(*) FROM invoice").fetchone() == (0,)
    assert connection.execute("SELECT count(*) FROM detail").fetchone() == (0,)
