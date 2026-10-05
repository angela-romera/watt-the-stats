from datetime import date
from unittest.mock import MagicMock

import pytest

from src.config import DatabaseSettings, EmailSettings
from src.email_report import (
    CHEAP_PRICE_THRESHOLD_EUR_PER_KWH,
    EXPENSIVE_PRICE_THRESHOLD_EUR_PER_KWH,
    latest_invoice_recipient,
    latest_invoice_report,
    send_empty_invoice_report,
    send_invoice_report,
)


DATABASE = DatabaseSettings("host", "database", "user", "database-secret", schema="public")
EMAIL = EmailSettings("ohm.my.god.ep@gmail.com", "email-secret", "smtp.gmail.com", 465)


def database_cursor(monkeypatch, row, additional_rows=()):
    connect = MagicMock()
    cursor = connect.return_value.__enter__.return_value.cursor.return_value.__enter__.return_value
    cursor.fetchone.side_effect = [row, *additional_rows]
    monkeypatch.setattr("src.email_report.psycopg.connect", connect)
    return cursor


def test_latest_invoice_recipient_uses_client_one_and_newest_invoice(monkeypatch):
    cursor = database_cursor(monkeypatch, (27, "client@example.com"))

    recipient = latest_invoice_recipient(DATABASE)

    assert recipient.invoice_id == 27
    assert recipient.email_to == "client@example.com"
    query = cursor.execute.call_args.args[0].as_string()
    assert "c.email_to" in query
    assert "ORDER BY i.id DESC LIMIT 1" in query
    assert cursor.execute.call_args.args[1] == (1,)


@pytest.mark.parametrize("row,error", [(None, LookupError), ((27, " "), ValueError)])
def test_latest_invoice_recipient_rejects_missing_invoice_or_email(monkeypatch, row, error):
    database_cursor(monkeypatch, row)

    with pytest.raises(error):
        latest_invoice_recipient(DATABASE)


def test_send_empty_invoice_report_uses_gmail_ssl(monkeypatch):
    database_cursor(monkeypatch, (27, "client@example.com"))
    smtp = MagicMock()
    monkeypatch.setattr("src.email_report.smtplib.SMTP_SSL", smtp)

    recipient = send_empty_invoice_report(DATABASE, EMAIL)

    assert recipient.invoice_id == 27
    smtp.assert_called_once_with("smtp.gmail.com", 465, timeout=30)
    client = smtp.return_value.__enter__.return_value
    client.login.assert_called_once_with("ohm.my.god.ep@gmail.com", "email-secret")
    message = client.send_message.call_args.args[0]
    assert message["From"] == "ohm.my.god.ep@gmail.com"
    assert message["To"] == "client@example.com"
    assert message.get_content().strip() == ""


def test_latest_invoice_report_sums_wh_and_cost(monkeypatch):
    cursor = database_cursor(
        monkeypatch,
        (
            27,
            "client@example.com",
            date(2026, 1, 1),
            date(2026, 1, 31),
            "2.0TD",
            "1234.5",
            "42.123",
            "200",
            "1000",
            "34.5",
        ),
        additional_rows=[None],
    )

    report = latest_invoice_report(DATABASE)

    assert str(report.consumption_kwh) == "1.2345"
    assert str(report.cost_eur) == "42.123"
    assert str(report.cheap_consumption_kwh) == "0.2"
    assert str(report.medium_consumption_kwh) == "1"
    assert str(report.expensive_consumption_kwh) == "0.0345"
    query = cursor.execute.call_args_list[0].args[0].as_string()
    assert "SUM(d.consumption)" in query
    assert "SUM(d.cost_per_hour)" in query
    assert cursor.execute.call_args_list[0].args[1] == (
        CHEAP_PRICE_THRESHOLD_EUR_PER_KWH,
        CHEAP_PRICE_THRESHOLD_EUR_PER_KWH,
        EXPENSIVE_PRICE_THRESHOLD_EUR_PER_KWH,
        EXPENSIVE_PRICE_THRESHOLD_EUR_PER_KWH,
        1,
    )


def test_send_invoice_report_has_kwh_and_eur_totals(monkeypatch):
    database_cursor(
        monkeypatch,
        (
            27,
            "client@example.com",
            date(2026, 1, 1),
            date(2026, 1, 31),
            "2.0TD",
            "1234.5",
            "42.123",
            "200",
            "1000",
            "34.5",
        ),
        additional_rows=[None],
    )
    smtp = MagicMock()
    monkeypatch.setattr("src.email_report.smtplib.SMTP_SSL", smtp)

    send_invoice_report(DATABASE, EMAIL)

    message = smtp.return_value.__enter__.return_value.send_message.call_args.args[0]
    assert "Consum total: 1.235 kWh" in message.get_content()
    assert "Cost total: 42.12 EUR" in message.get_content()
    assert "Preu baix (≤ 0.15 EUR/kWh): 0.200 kWh (16.2%)" in message.get_content()
    assert "Preu mitjà (> 0.15 i < 0.25 EUR/kWh): 1.000 kWh (81.0%)" in message.get_content()
    assert "Preu alt (≥ 0.25 EUR/kWh): 0.035 kWh (2.8%)" in message.get_content()
    html = message.get_body("html").get_content()
    assert 'src="cid:consum-per-preu"' in html
    charts = [part for part in message.walk() if part.get_content_type() == "image/png"]
    assert len(charts) == 1
    assert all(chart.get_payload(decode=True).startswith(b"\x89PNG\r\n\x1a\n") for chart in charts)


def test_report_includes_previous_year_summary_when_detail_dates_exist(monkeypatch):
    database_cursor(
        monkeypatch,
        (
            27,
            "client@example.com",
            date(2026, 1, 1),
            date(2026, 1, 31),
            "2.0TD",
            "1000",
            "10",
            "500",
            "500",
            "0",
        ),
        additional_rows=[("200", "500", "100")],
    )
    smtp = MagicMock()
    monkeypatch.setattr("src.email_report.smtplib.SMTP_SSL", smtp)

    send_invoice_report(DATABASE, EMAIL)

    message = smtp.return_value.__enter__.return_value.send_message.call_args.args[0]
    assert "Preu baix — període actual: 0.500 kWh" in message.get_content()
    assert "Preu baix — any anterior: 0.200 kWh" in message.get_content()
    assert "Preu mitjà — període actual: 0.500 kWh" in message.get_content()
    assert "Preu mitjà — any anterior: 0.500 kWh" in message.get_content()
    assert "Preu alt — període actual: 0.000 kWh" in message.get_content()
    assert "Preu alt — any anterior: 0.100 kWh" in message.get_content()
    html = message.get_body("html").get_content()
    assert 'src="cid:comparativa-consum-per-preu"' in html
    charts = [part for part in message.walk() if part.get_content_type() == "image/png"]
    assert len(charts) == 2
