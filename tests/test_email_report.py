from datetime import date, time
from decimal import Decimal
from unittest.mock import MagicMock

from src.config import DatabaseSettings, EmailSettings
from src.email_report import latest_invoice_report, send_invoice_report

DATABASE = DatabaseSettings("host", "database", "user", "secret")
EMAIL = EmailSettings("sender@example.com", "email-secret", "smtp.example.com", 465)


def install_database(
    monkeypatch, current_rows, last_year_rows, previous_invoice=None, previous_rows=()
):
    connect = MagicMock()
    cursor = connect.return_value.__enter__.return_value.cursor.return_value.__enter__.return_value
    cursor.fetchone.side_effect = [
        (27, "client@example.com", date(2026, 1, 1), date(2026, 1, 31), "2.0TD"),
        previous_invoice,
    ]
    cursor.fetchall.side_effect = [current_rows, last_year_rows, previous_rows]
    monkeypatch.setattr("src.email_report.psycopg.connect", connect)
    return cursor


def rows_for_day(day):
    return [
        (day, time(0), Decimal("200"), Decimal("0.15"), Decimal("0.03")),
        (day, time(1), Decimal("1000"), Decimal("0.20"), Decimal("0.20")),
        (day, time(20), Decimal("34.5"), Decimal("0.30"), Decimal("0.01035")),
    ]


def test_report_uses_detail_rows_for_totals_and_price_boundaries(monkeypatch):
    rows = rows_for_day(date(2026, 1, 1))
    install_database(monkeypatch, rows, [], previous_invoice=None)

    report = latest_invoice_report(DATABASE)

    assert report.consumption_kwh == Decimal("1.2345")
    assert report.cost_eur == Decimal("0.24035")
    assert report.analysis.distribution.cheap_kwh == Decimal("0.2")
    assert report.analysis.distribution.medium_kwh == Decimal("1")
    assert report.analysis.distribution.expensive_kwh == Decimal("0.0345")
    assert report.analysis.average_price_eur_per_kwh == Decimal("0.24035") / Decimal("1.2345")


def test_report_can_target_a_specific_invoice_id(monkeypatch):
    cursor = install_database(monkeypatch, rows_for_day(date(2026, 1, 1)), [])

    report = latest_invoice_report(DATABASE, invoice_id=27)

    assert report.invoice_id == 27
    assert cursor.execute.call_args_list[0].args[1] == (1, 27)


def test_previous_year_section_only_appears_with_full_date_coverage(monkeypatch):
    current = rows_for_day(date(2026, 1, 1))
    complete_previous = [
        (date(2025, 1, day), time(8), Decimal("1000"), Decimal("0.10"), Decimal("0.10"))
        for day in range(1, 32)
    ]
    install_database(monkeypatch, current, complete_previous)
    smtp = MagicMock()
    monkeypatch.setattr("src.email_report.smtplib.SMTP_SSL", smtp)

    send_invoice_report(DATABASE, EMAIL)

    message = smtp.return_value.__enter__.return_value.send_message.call_args.args[0]
    text = message.get_body("plain").get_content()
    html = message.get_body("html").get_content()
    assert "Comparativa amb el mateix període de l'any anterior" in text
    assert 'src="cid:comparativa-consum-per-preu"' in html
    charts = [part for part in message.walk() if part.get_content_type() == "image/png"]
    assert len(charts) == 2


def test_incomplete_previous_year_period_is_omitted(monkeypatch):
    install_database(
        monkeypatch,
        rows_for_day(date(2026, 1, 1)),
        [(date(2025, 1, 1), time(8), Decimal("1000"), Decimal("0.10"), Decimal("0.10"))],
    )
    smtp = MagicMock()
    monkeypatch.setattr("src.email_report.smtplib.SMTP_SSL", smtp)

    send_invoice_report(DATABASE, EMAIL)

    message = smtp.return_value.__enter__.return_value.send_message.call_args.args[0]
    assert (
        "Comparativa amb el mateix període de l'any anterior"
        not in message.get_body("plain").get_content()
    )
    assert len([part for part in message.walk() if part.get_content_type() == "image/png"]) == 1


def test_hourly_consumption_section_is_removed(monkeypatch):
    install_database(monkeypatch, rows_for_day(date(2026, 1, 1)), [])
    smtp = MagicMock()
    monkeypatch.setattr("src.email_report.smtplib.SMTP_SSL", smtp)

    send_invoice_report(DATABASE, EMAIL)

    message = smtp.return_value.__enter__.return_value.send_message.call_args.args[0]
    plain = message.get_body("plain").get_content()
    html = message.get_body("html").get_content()
    assert "Quan has consumit?" not in html
    assert "<tr><td style=\"width:100px" not in html
    assert "Dies amb més consum" in plain


def test_previous_invoice_is_found_by_dates_and_daily_values_render(monkeypatch):
    current = [(date(2026, 1, 1), time(20), Decimal("3100"), Decimal("0.20"), Decimal("0.62"))]
    previous_rows = [
        (date(2025, 12, 2), time(20), Decimal("1000"), Decimal("0.20"), Decimal("0.20"))
    ]
    cursor = install_database(
        monkeypatch,
        current,
        [],
        previous_invoice=(26, date(2025, 12, 1), date(2025, 12, 10)),
        previous_rows=previous_rows,
    )
    smtp = MagicMock()
    monkeypatch.setattr("src.email_report.smtplib.SMTP_SSL", smtp)

    send_invoice_report(DATABASE, EMAIL)

    message = smtp.return_value.__enter__.return_value.send_message.call_args.args[0]
    text = message.get_body("plain").get_content()
    assert "Factura actual (01/01/2026 - 31/01/2026): 0.100 kWh/dia" in text
    assert "Factura anterior (01/12/2025 - 10/12/2025): 0.100 kWh/dia" in text
    assert "Preu mitjà" in text
    query = cursor.execute.call_args_list[3].args[0].as_string()
    assert "i.final_date < %s" in query
    assert "ORDER BY i.final_date DESC, i.initial_date DESC" in query
    assert cursor.execute.call_args_list[3].args[1] == (1, date(2026, 1, 1))
