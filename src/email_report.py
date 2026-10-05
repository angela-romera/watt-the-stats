"""Send invoice-report emails without changing invoice data."""

import smtplib
import struct
import zlib
from calendar import monthrange
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
    previous_period: "PreviousPeriod | None"


@dataclass(frozen=True)
class PreviousPeriod:
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
    previous_period = _previous_period_summary(
        database,
        client_id,
        _one_year_earlier(initial_date),
        _one_year_earlier(final_date),
    )
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
        previous_period=previous_period,
    )


def _previous_period_summary(
    database: DatabaseSettings,
    client_id: int,
    initial_date: date,
    final_date: date,
) -> PreviousPeriod | None:
    """Summarize the previous year's matching dates from detail rows."""
    tables = {
        name: sql.Identifier(database.schema, name) for name in ("invoice", "detail")
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
                "SELECT COALESCE(SUM(d.consumption) FILTER (WHERE d.price <= %s), 0), "
                "COALESCE(SUM(d.consumption) FILTER "
                "(WHERE d.price > %s AND d.price < %s), 0), "
                "COALESCE(SUM(d.consumption) FILTER (WHERE d.price >= %s), 0) "
                "FROM {invoice} i JOIN {detail} d ON d.id_invoice = i.id "
                "WHERE i.id_client = %s AND d.date >= %s AND d.date <= %s "
                "HAVING COUNT(d.id) > 0"
            ).format(**tables),
            (
                CHEAP_PRICE_THRESHOLD_EUR_PER_KWH,
                CHEAP_PRICE_THRESHOLD_EUR_PER_KWH,
                EXPENSIVE_PRICE_THRESHOLD_EUR_PER_KWH,
                EXPENSIVE_PRICE_THRESHOLD_EUR_PER_KWH,
                client_id,
                initial_date,
                final_date,
            ),
        )
        row = cursor.fetchone()
    if row is None:
        return None
    (
        cheap_consumption_wh,
        medium_consumption_wh,
        expensive_consumption_wh,
    ) = row
    return PreviousPeriod(
        cheap_consumption_kwh=Decimal(cheap_consumption_wh) / Decimal("1000"),
        medium_consumption_kwh=Decimal(medium_consumption_wh) / Decimal("1000"),
        expensive_consumption_kwh=Decimal(expensive_consumption_wh) / Decimal("1000"),
    )


def _one_year_earlier(value: date) -> date:
    """Shift a date back one calendar year, including leap-day periods."""
    year = value.year - 1
    return date(year, value.month, min(value.day, monthrange(year, value.month)[1]))


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
    comparison_text, comparison_html, comparison_bands = _previous_period_content(report, bands)
    chart_cid = "consum-per-preu"
    comparison_chart_cid = "comparativa-consum-per-preu"
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
        f"{comparison_text}"
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
        f"{comparison_html}"
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
    if comparison_bands is not None:
        message.get_payload()[-1].add_related(
            _price_band_comparison_png([(amount, color) for _label, amount, color in comparison_bands]),
            maintype="image",
            subtype="png",
            cid=f"<{comparison_chart_cid}>",
            filename="comparativa-consum-per-preu.png",
            disposition="inline",
        )
    return message


def _previous_period_content(
    report: InvoiceReport,
    current_bands: tuple[tuple[str, Decimal, str], ...],
) -> tuple[
    str,
    str,
    tuple[tuple[str, Decimal, str], ...] | None,
]:
    """Build the optional current-versus-previous-year price-band comparison."""
    previous = report.previous_period
    if previous is None:
        return "", "", None
    bands = (
        (
            f"Preu baix (≤ {CHEAP_PRICE_THRESHOLD_EUR_PER_KWH} EUR/kWh)",
            previous.cheap_consumption_kwh,
            CHEAP_PRICE_COLOR,
        ),
        (
            f"Preu mitjà (> {CHEAP_PRICE_THRESHOLD_EUR_PER_KWH} i < "
            f"{EXPENSIVE_PRICE_THRESHOLD_EUR_PER_KWH} EUR/kWh)",
            previous.medium_consumption_kwh,
            MEDIUM_PRICE_COLOR,
        ),
        (
            f"Preu alt (≥ {EXPENSIVE_PRICE_THRESHOLD_EUR_PER_KWH} EUR/kWh)",
            previous.expensive_consumption_kwh,
            EXPENSIVE_PRICE_COLOR,
        ),
    )
    comparison_bands = (
        ("Preu baix — període actual", current_bands[0][1], "#2E7D32"),
        ("Preu baix — any anterior", bands[0][1], "#81C784"),
        ("Preu mitjà — període actual", current_bands[1][1], "#F9A825"),
        ("Preu mitjà — any anterior", bands[1][1], "#FFE082"),
        ("Preu alt — període actual", current_bands[2][1], "#C62828"),
        ("Preu alt — any anterior", bands[2][1], "#EF9A9A"),
    )
    comparison_text = "\n".join(
        f"- {label}: {amount.quantize(Decimal('0.001'), rounding=ROUND_HALF_UP)} kWh"
        for label, amount, _color in comparison_bands
    )
    comparison_legend = "".join(
        "<li>"
        f"<span style=\"color: {color};\">&#9632;</span> {label}: "
        f"{amount.quantize(Decimal('0.001'), rounding=ROUND_HALF_UP)} kWh"
        "</li>"
        for label, amount, color in comparison_bands
    )
    comparison_chart_cid = "comparativa-consum-per-preu"
    text = (
        "\nComparativa del consum per franja de preu:\n"
        f"{comparison_text}\n"
    )
    html = (
        "<h3>Comparativa del consum per franja de preu</h3>"
        f"<img src=\"cid:{comparison_chart_cid}\" "
        "alt=\"Gràfic de columnes del consum per franja de preu i període\" "
        "width=\"360\" height=\"280\">"
        f"<ul>{comparison_legend}</ul>"
    )
    return text, html, comparison_bands


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


def _price_band_comparison_png(bands: list[tuple[Decimal, str]]) -> bytes:
    """Create a six-column PNG chart in the supplied low-to-high price order."""
    size = 360
    baseline = 235
    maximum_height = 180
    maximum = max((amount for amount, _color in bands), default=Decimal("1"))
    maximum = max(maximum, Decimal("1"))
    bar_left_edges = (25, 80, 135, 190, 245, 300)
    bar_width = 35
    bar_specs = [
        (left, int(amount / maximum * maximum_height), _hex_to_rgb(color))
        for left, (amount, color) in zip(bar_left_edges, bands, strict=True)
    ]
    rows = []
    for y in range(size):
        row = bytearray(b"\x00")
        for x in range(size):
            pixel = (255, 255, 255)
            if y == baseline and 15 <= x <= 345:
                pixel = (80, 80, 80)
            for left, height, color in bar_specs:
                if left <= x < left + bar_width and baseline - height <= y < baseline:
                    pixel = color
                    break
            row.extend(pixel)
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
