from unittest.mock import MagicMock

import pytest

from src.config import DatabaseSettings, EmailSettings
from src.email_report import latest_invoice_recipient, send_empty_invoice_report


DATABASE = DatabaseSettings("host", "database", "user", "database-secret", schema="public")
EMAIL = EmailSettings("ohm.my.god.ep@gmail.com", "email-secret", "smtp.gmail.com", 465)


def database_cursor(monkeypatch, row):
    connect = MagicMock()
    cursor = connect.return_value.__enter__.return_value.cursor.return_value.__enter__.return_value
    cursor.fetchone.return_value = row
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
