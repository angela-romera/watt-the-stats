from datetime import date, time
from decimal import Decimal
from unittest.mock import MagicMock

import pytest

from src.config import DatabaseSettings, load_database_settings
from src.process_data import Detail, Invoice
from src.upload_data import UploadConflict, missing_details, upload_invoices


def detail(consumption="1.0", price="0.2", cost="0.2"):
    return Detail(
        date(2024, 1, 1),
        time(0),
        Decimal(consumption),
        Decimal(price),
        Decimal(cost),
    )


def invoice(*details):
    return Invoice(
        "ES123",
        date(2024, 1, 1),
        date(2024, 1, 2),
        "2.0TD",
        tuple(details),
    )


def stored(item):
    return item.insert_values(1)[:-1]


def test_missing_details_skips_exact_duplicate():
    item = detail()

    assert missing_details(invoice(item), [stored(item)]) == []


def test_missing_details_returns_strict_subset_rows():
    first = detail()
    second = detail(price="0.3")

    assert missing_details(invoice(first, second), [stored(first)]) == [stored(second)]


@pytest.mark.parametrize(
    ("consumption", "price", "cost"),
    [("2.0", "0.2", "0.2"), ("1.0", "0.3", "0.2"), ("1.0", "0.2", "0.3")],
)
def test_missing_details_rejects_changed_values(consumption, price, cost):
    source = detail()
    changed = detail(consumption, price, cost)

    with pytest.raises(UploadConflict, match="Stored details differ"):
        missing_details(invoice(source), [stored(changed)])


def test_missing_details_rejects_extra_stored_rows():
    source = detail()
    extra = detail(price="0.3")

    with pytest.raises(UploadConflict, match="Stored details differ"):
        missing_details(invoice(source), [stored(source), stored(extra)])


def test_missing_details_preserves_repeated_row_multiplicity():
    item = detail()

    assert missing_details(invoice(item, item), [stored(item)]) == [stored(item)]
    assert missing_details(invoice(item, item), [stored(item), stored(item)]) == []


@pytest.mark.parametrize("clients", [[(2,)], [], [(1,), (2,)]])
def test_upload_matches_cups_across_configured_users(monkeypatch, clients):
    connect = MagicMock()
    monkeypatch.setattr("src.upload_data.psycopg.connect", connect)
    cursor = connect.return_value.__enter__.return_value.cursor.return_value.__enter__.return_value
    cursor.fetchall.side_effect = [clients, []]
    cursor.fetchone.return_value = (10,)
    database = DatabaseSettings("host", "database", "user", "secret")
    if len(clients) == 1:
        result = upload_invoices([invoice(detail())], database, ["first", "second"])
        assert result.invoices_inserted == 1
    else:
        with pytest.raises(UploadConflict, match="exactly one"):
            upload_invoices([invoice(detail())], database, ["first", "second"])
        cursor.executemany.assert_not_called()
    lookup = cursor.execute.call_args_list[2]
    assert "l.username = ANY(%s)" in lookup.args[0].as_string()
    assert lookup.args[1] == ("ES123", ["first", "second"])


def test_database_settings_hide_password_in_repr(monkeypatch):
    monkeypatch.setattr("src.config.load_dotenv", lambda *_args, **_kwargs: None)
    for name in ("PORT_DB_WTS", "SCHEMA_DB_WTS", "SSLMODE_DB_WTS"):
        monkeypatch.delenv(name, raising=False)
    for name, value in {
        "SERVER_WTS": "db.example",
        "DB_WTS_ELEC": "electricity",
        "USER_DB_WTS": "writer",
        "PWD_DB_WTS": "secret-password",
    }.items():
        monkeypatch.setenv(name, value)

    settings = load_database_settings()

    assert settings.port == 5432
    assert settings.schema == "public"
    assert "secret-password" not in repr(settings)


def test_database_settings_reject_missing_environment(monkeypatch):
    monkeypatch.setattr("src.config.load_dotenv", lambda *_args, **_kwargs: None)
    for name in ("SERVER_WTS", "DB_WTS_ELEC", "USER_DB_WTS", "PWD_DB_WTS"):
        monkeypatch.delenv(name, raising=False)

    with pytest.raises(ValueError, match="Missing PostgreSQL settings"):
        load_database_settings()


@pytest.mark.parametrize("port", ["0", "65536", "invalid"])
def test_database_settings_reject_invalid_port(monkeypatch, port):
    monkeypatch.setattr("src.config.load_dotenv", lambda *_args, **_kwargs: None)
    for name, value in {
        "SERVER_WTS": "db.example",
        "DB_WTS_ELEC": "electricity",
        "USER_DB_WTS": "writer",
        "PWD_DB_WTS": "secret-password",
    }.items():
        monkeypatch.setenv(name, value)
    monkeypatch.setenv("PORT_DB_WTS", port)

    if port == "invalid":
        with pytest.raises(ValueError):
            load_database_settings()
    else:
        with pytest.raises(ValueError, match="PORT_DB_WTS must be between"):
            load_database_settings()
