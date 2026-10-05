"""Send invoice-report emails without changing invoice data."""

import smtplib
import struct
import zlib
from dataclasses import dataclass
from datetime import date
from decimal import Decimal, ROUND_HALF_UP
from email.message import EmailMessage
from html import escape
from math import atan2, pi

import psycopg
from psycopg import sql

from src.config import DatabaseSettings, EmailSettings

CHEAP_PRICE_THRESHOLD_EUR_PER_KWH = Decimal("0.15")
EXPENSIVE_PRICE_THRESHOLD_EUR_PER_KWH = Decimal("0.25")
CHEAP_PRICE_COLOR = "#43A047"
MEDIUM_PRICE_COLOR = "#F9A825"
EXPENSIVE_PRICE_COLOR = "#E53935"


@dataclass(frozen=True)
class ReportRecipient:
    invoice_id: int
    email_to: str


@dataclass(frozen=True)
class InvoiceReport:
    invoice_id: int
    email_to: str
    initial_date: date
    final_date: date
    tariff: str
    consumption_kwh: Decimal
    cost_eur: Decimal
    cheap_consumption_kwh: Decimal
    medium_consumption_kwh: Decimal
    expensive_consumption_kwh: Decimal


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
    message["Subject"] = "Resum de factura"
    message.set_content("")
    with smtplib.SMTP_SSL(email.host, email.port, timeout=30) as smtp:
        smtp.login(email.sender, email.password)
        smtp.send_message(message)
    return recipient


def latest_invoice_report(database: DatabaseSettings, client_id: int = 1) -> InvoiceReport:
    """Summarize the stored details for a client's most recently inserted invoice."""
    if isinstance(client_id, bool) or client_id < 1:
        raise ValueError("client_id must be a positive integer")
    tables = {
        name: sql.Identifier(database.schema, name)
        for name in ("client", "invoice", "detail")
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
                "SELECT i.id, c.email_to, i.initial_date, i.final_date, i.tariff, "
                "COALESCE(SUM(d.consumption), 0), COALESCE(SUM(d.cost_per_hour), 0), "
                "COALESCE(SUM(d.consumption) FILTER (WHERE d.price <= %s), 0), "
                "COALESCE(SUM(d.consumption) FILTER "
                "(WHERE d.price > %s AND d.price < %s), 0), "
                "COALESCE(SUM(d.consumption) FILTER (WHERE d.price >= %s), 0) "
                "FROM {invoice} i "
                "JOIN {client} c ON c.id = i.id_client "
                "LEFT JOIN {detail} d ON d.id_invoice = i.id "
                "WHERE i.id_client = %s "
                "GROUP BY i.id, c.email_to, i.initial_date, i.final_date, i.tariff "
                "ORDER BY i.id DESC LIMIT 1"
            ).format(**tables),
            (
                CHEAP_PRICE_THRESHOLD_EUR_PER_KWH,
                CHEAP_PRICE_THRESHOLD_EUR_PER_KWH,
                EXPENSIVE_PRICE_THRESHOLD_EUR_PER_KWH,
                EXPENSIVE_PRICE_THRESHOLD_EUR_PER_KWH,
                client_id,
            ),
        )
        row = cursor.fetchone()
    if row is None:
        raise LookupError(f"No invoice found for client_id={client_id}")
    (
        invoice_id,
        email_to,
        initial_date,
        final_date,
        tariff,
        consumption_wh,
        cost_eur,
        cheap_consumption_wh,
        medium_consumption_wh,
        expensive_consumption_wh,
    ) = row
    if not isinstance(email_to, str) or not email_to.strip():
        raise ValueError(f"Client {client_id} has no email_to address")
    return InvoiceReport(
        invoice_id=invoice_id,
        email_to=email_to.strip(),
        initial_date=initial_date,
        final_date=final_date,
        tariff=tariff,
        consumption_kwh=Decimal(consumption_wh) / Decimal("1000"),
        cost_eur=Decimal(cost_eur),
        cheap_consumption_kwh=Decimal(cheap_consumption_wh) / Decimal("1000"),
        medium_consumption_kwh=Decimal(medium_consumption_wh) / Decimal("1000"),
        expensive_consumption_kwh=Decimal(expensive_consumption_wh) / Decimal("1000"),
    )


def _report_message(report: InvoiceReport, sender: str) -> EmailMessage:
    """Build the plain-text initial version of an invoice summary email."""
    consumption = report.consumption_kwh.quantize(Decimal("0.001"), rounding=ROUND_HALF_UP)
    cost = report.cost_eur.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    bands = (
        (
            f"Preu baix (≤ {CHEAP_PRICE_THRESHOLD_EUR_PER_KWH} EUR/kWh)",
            report.cheap_consumption_kwh,
            CHEAP_PRICE_COLOR,
        ),
        (
            f"Preu mitjà (> {CHEAP_PRICE_THRESHOLD_EUR_PER_KWH} i < "
            f"{EXPENSIVE_PRICE_THRESHOLD_EUR_PER_KWH} EUR/kWh)",
            report.medium_consumption_kwh,
            MEDIUM_PRICE_COLOR,
        ),
        (
            f"Preu alt (≥ {EXPENSIVE_PRICE_THRESHOLD_EUR_PER_KWH} EUR/kWh)",
            report.expensive_consumption_kwh,
            EXPENSIVE_PRICE_COLOR,
        ),
    )
    distribution = "\n".join(
        f"- {label}: {amount.quantize(Decimal('0.001'), rounding=ROUND_HALF_UP)} kWh "
        f"({_consumption_percentage(amount, report.consumption_kwh)}%)"
        for label, amount, _color in bands
    )
    chart_cid = "consum-per-preu"
    message = EmailMessage()
    message["From"] = sender
    message["To"] = report.email_to
    message["Subject"] = (
        f"Resum de factura {report.initial_date:%d/%m/%Y} - {report.final_date:%d/%m/%Y}"
    )
    message.set_content(
        "Resum de la factura\n\n"
        f"Període: {report.initial_date:%d/%m/%Y} - {report.final_date:%d/%m/%Y}\n"
        f"Tarifa: {report.tariff}\n"
        f"Consum total: {consumption} kWh\n"
        f"Cost total: {cost} EUR\n"
        "\nDistribució del consum segons el preu:\n"
        f"{distribution}\n"
    )
    legend = "".join(
        "<li>"
        f"<span style=\"color: {color};\">&#9679;</span> "
        f"{escape(label)}: "
        f"{amount.quantize(Decimal('0.001'), rounding=ROUND_HALF_UP)} kWh "
        f"({_consumption_percentage(amount, report.consumption_kwh)}%)"
        "</li>"
        for label, amount, color in bands
    )
    message.add_alternative(
        "<html><body>"
        "<h2>Resum de la factura</h2>"
        f"<p><strong>Període:</strong> {report.initial_date:%d/%m/%Y} - "
        f"{report.final_date:%d/%m/%Y}<br>"
        f"<strong>Tarifa:</strong> {escape(report.tariff)}<br>"
        f"<strong>Consum total:</strong> {consumption} kWh<br>"
        f"<strong>Cost total:</strong> {cost} EUR</p>"
        "<h3>Distribució del consum segons el preu</h3>"
        f"<img src=\"cid:{chart_cid}\" alt=\"Gràfic de sectors del consum per preu\" "
        "width=\"280\" height=\"280\">"
        f"<ul>{legend}</ul>"
        "</body></html>",
        subtype="html",
    )
    message.get_payload()[-1].add_related(
        _pie_chart_png([(amount, color) for _label, amount, color in bands]),
        maintype="image",
        subtype="png",
        cid=f"<{chart_cid}>",
        filename="distribucio-consum-per-preu.png",
        disposition="inline",
    )
    return message


def _consumption_percentage(amount: Decimal, total: Decimal) -> Decimal:
    """Return a one-decimal consumption share without dividing by zero."""
    if not total:
        return Decimal("0.0")
    return (amount * Decimal("100") / total).quantize(Decimal("0.1"), rounding=ROUND_HALF_UP)


def _pie_chart_png(bands: list[tuple[Decimal, str]]) -> bytes:
    """Create a dependency-free PNG pie chart for inline email use."""
    size = 280
    center = size // 2
    radius = 125
    total = sum(amount for amount, _color in bands)
    rgb_bands = [(_hex_to_rgb(color), amount / total if total else Decimal(0)) for amount, color in bands]
    rows = []
    for y in range(size):
        row = bytearray(b"\x00")
        for x in range(size):
            dx, dy = x - center, y - center
            if dx * dx + dy * dy > radius * radius:
                row.extend((255, 255, 255))
                continue
            if not total:
                row.extend((158, 158, 158))
                continue
            angle = (atan2(dy, dx) + pi / 2) % (2 * pi)
            portion = Decimal(str(angle / (2 * pi)))
            cumulative = Decimal(0)
            for index, (color, share) in enumerate(rgb_bands):
                cumulative += share
                if portion < cumulative or index == len(rgb_bands) - 1:
                    row.extend(color)
                    break
        rows.append(bytes(row))
    payload = zlib.compress(b"".join(rows), level=9)
    return b"".join(
        (
            b"\x89PNG\r\n\x1a\n",
            _png_chunk(b"IHDR", struct.pack(">IIBBBBB", size, size, 8, 2, 0, 0, 0)),
            _png_chunk(b"IDAT", payload),
            _png_chunk(b"IEND", b""),
        )
    )


def _hex_to_rgb(color: str) -> tuple[int, int, int]:
    """Convert a six-digit CSS hex color to the RGB bytes used by PNG."""
    return tuple(int(color[index : index + 2], 16) for index in (1, 3, 5))


def _png_chunk(kind: bytes, data: bytes) -> bytes:
    return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data))


def send_invoice_report(
    database: DatabaseSettings,
    email: EmailSettings,
    client_id: int = 1,
) -> InvoiceReport:
    """Send the consumption and cost summary for the latest invoice."""
    report = latest_invoice_report(database, client_id)
    with smtplib.SMTP_SSL(email.host, email.port, timeout=30) as smtp:
        smtp.login(email.sender, email.password)
        smtp.send_message(_report_message(report, email.sender))
    return report
