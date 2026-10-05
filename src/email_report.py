"""Send invoice-report emails without changing invoice data."""

import smtplib
from dataclasses import dataclass
from email.message import EmailMessage

import psycopg
from psycopg import sql

from src.config import DatabaseSettings, EmailSettings


@dataclass(frozen=True)
class ReportRecipient:
    invoice_id: int
    email_to: str


def latest_invoice_recipient(
    database: DatabaseSettings, client_id: int = 1
) -> ReportRecipient:
    """Return the recipient for a client's most recently inserted invoice."""
    if isinstance(client_id, bool) or client_id < 1:
        raise ValueError("client_id must be a positive integer")
    tables = {
        name: sql.Identifier(database.schema, name) for name in ("client", "invoice")
    }
    with psycopg.connect(
        host=database.host,
        dbname=database.dbname,
        user=database.user,
        password=database.password,
        port=database.port,
        sslmode=database.sslmode,
        connect_timeout=10,
    ) as connection, connection.cursor() as cursor:
        cursor.execute(
            sql.SQL(
                "SELECT i.id, c.email_to "
                "FROM {invoice} i JOIN {client} c ON c.id = i.id_client "
                "WHERE i.id_client = %s ORDER BY i.id DESC LIMIT 1"
            ).format(**tables),
            (client_id,),
        )
        row = cursor.fetchone()
    if row is None:
        raise LookupError(f"No invoice found for client_id={client_id}")
    invoice_id, email_to = row
    if not isinstance(email_to, str) or not email_to.strip():
        raise ValueError(f"Client {client_id} has no email_to address")
    return ReportRecipient(invoice_id=invoice_id, email_to=email_to.strip())


def send_empty_invoice_report(
    database: DatabaseSettings,
    email: EmailSettings,
    client_id: int = 1,
) -> ReportRecipient:
    """Send a deliberately empty report to the latest invoice's client."""
    recipient = latest_invoice_recipient(database, client_id)
    message = EmailMessage()
    message["From"] = email.sender
    message["To"] = recipient.email_to
    message["Subject"] = "Invoice report"
    message.set_content("")
    with smtplib.SMTP_SSL(email.host, email.port, timeout=30) as smtp:
        smtp.login(email.sender, email.password)
        smtp.send_message(message)
    return recipient
