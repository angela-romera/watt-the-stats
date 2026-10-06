"""Send invoice-report emails without changing invoice data."""

import smtplib
import struct
import zlib
from calendar import monthrange
from dataclasses import dataclass
from datetime import date
from decimal import ROUND_HALF_UP, Decimal
from email.message import EmailMessage
from html import escape
from math import atan2, pi

import psycopg
from psycopg import sql

from src.config import DatabaseSettings, EmailSettings
from src.report_calculations import (
    BillAnalysis,
    BillComparison,
    SavingsEstimate,
    calculate_bill_analysis,
    calculate_bill_comparison,
    calculate_percentage_change,
    estimate_potential_savings,
    generate_bill_insights,
    generate_diagnostics,
    generate_recommendations,
)

CHEAP_PRICE_THRESHOLD_EUR_PER_KWH = Decimal("0.15")
EXPENSIVE_PRICE_THRESHOLD_EUR_PER_KWH = Decimal("0.25")
CHEAP_PRICE_COLOR = "#43A047"
MEDIUM_PRICE_COLOR = "#F9A825"
EXPENSIVE_PRICE_COLOR = "#E53935"


class MissingReportRecipientError(ValueError):
    """Raised when a billing report has no recipient address."""


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
    previous_invoice: "PreviousInvoice | None"
    analysis: BillAnalysis
    comparison: BillComparison
    savings: SavingsEstimate | None
    insights: tuple[str, ...]
    diagnostics: tuple[str, ...]
    recommendations: tuple[str, ...]


@dataclass(frozen=True)
class PreviousPeriod:
    analysis: BillAnalysis


@dataclass(frozen=True)
class PreviousInvoice:
    invoice_id: int
    initial_date: date
    final_date: date
    analysis: BillAnalysis


def latest_invoice_recipient(database: DatabaseSettings, client_id: int = 1) -> ReportRecipient:
    """Return the recipient for a client's most recently inserted invoice."""
    if isinstance(client_id, bool) or client_id < 1:
        raise ValueError("client_id must be a positive integer")
    tables = {name: sql.Identifier(database.schema, name) for name in ("client", "invoice")}
    with (
        psycopg.connect(
            host=database.host,
            dbname=database.dbname,
            user=database.user,
            password=database.password,
            port=database.port,
            sslmode=database.sslmode,
            connect_timeout=10,
        ) as connection,
        connection.cursor() as cursor,
    ):
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
        raise MissingReportRecipientError(f"Client {client_id} has no email_to address")
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


def latest_invoice_report(
    database: DatabaseSettings, client_id: int = 1, invoice_id: int | None = None
) -> InvoiceReport:
    """Summarize a client's latest invoice, or a requested invoice ID."""
    if isinstance(client_id, bool) or client_id < 1:
        raise ValueError("client_id must be a positive integer")
    if invoice_id is not None and (isinstance(invoice_id, bool) or invoice_id < 1):
        raise ValueError("invoice_id must be a positive integer")
    tables = {
        name: sql.Identifier(database.schema, name) for name in ("client", "invoice", "detail")
    }
    with (
        psycopg.connect(
            host=database.host,
            dbname=database.dbname,
            user=database.user,
            password=database.password,
            port=database.port,
            sslmode=database.sslmode,
            connect_timeout=10,
        ) as connection,
        connection.cursor() as cursor,
    ):
        cursor.execute(
            sql.SQL(
                "SELECT i.id, c.email_to, i.initial_date, i.final_date, i.tariff "
                "FROM {invoice} i JOIN {client} c ON c.id = i.id_client "
                "WHERE i.id_client = %s {invoice_filter} "
                "ORDER BY i.id DESC LIMIT 1"
            ).format(
                **tables,
                invoice_filter=(
                    sql.SQL("AND i.id = %s") if invoice_id is not None else sql.SQL("")
                ),
            ),
            (client_id, invoice_id) if invoice_id is not None else (client_id,),
        )
        row = cursor.fetchone()
        if row is not None:
            current_id = row[0]
            cursor.execute(
                sql.SQL(
                    "SELECT d.date, d.hour, d.consumption, d.price, d.cost_per_hour "
                    "FROM {detail} d WHERE d.id_invoice = %s ORDER BY d.date, d.hour"
                ).format(detail=tables["detail"]),
                (current_id,),
            )
            current_details = cursor.fetchall()
    if row is None:
        raise LookupError(f"No invoice found for client_id={client_id}")
    (
        invoice_id,
        email_to,
        initial_date,
        final_date,
        tariff,
    ) = row
    if not isinstance(email_to, str) or not email_to.strip():
        raise MissingReportRecipientError(f"Client {client_id} has no email_to address")
    current_analysis = calculate_bill_analysis(current_details, initial_date, final_date)
    previous_period = _previous_period_summary(
        database,
        client_id,
        _one_year_earlier(initial_date),
        _one_year_earlier(final_date),
    )
    previous_invoice = _previous_invoice_summary(database, client_id, initial_date)
    comparison = calculate_bill_comparison(
        current_analysis, previous_invoice.analysis if previous_invoice else None
    )
    savings = estimate_potential_savings(current_details)
    insights = generate_bill_insights(current_analysis, comparison, savings)
    diagnostics = generate_diagnostics(current_analysis, comparison)
    recommendations = generate_recommendations(current_analysis, savings)
    return InvoiceReport(
        invoice_id=invoice_id,
        email_to=email_to.strip(),
        initial_date=initial_date,
        final_date=final_date,
        tariff=tariff,
        consumption_kwh=current_analysis.total_kwh,
        cost_eur=current_analysis.total_cost_eur,
        cheap_consumption_kwh=current_analysis.distribution.cheap_kwh,
        medium_consumption_kwh=current_analysis.distribution.medium_kwh,
        expensive_consumption_kwh=current_analysis.distribution.expensive_kwh,
        previous_period=previous_period,
        previous_invoice=previous_invoice,
        analysis=current_analysis,
        comparison=comparison,
        savings=savings,
        insights=insights,
        diagnostics=diagnostics,
        recommendations=recommendations,
    )


def _previous_period_summary(
    database: DatabaseSettings,
    client_id: int,
    initial_date: date,
    final_date: date,
) -> PreviousPeriod | None:
    """Summarize a fully represented equivalent date range from detail rows."""
    tables = {name: sql.Identifier(database.schema, name) for name in ("invoice", "detail")}
    with (
        psycopg.connect(
            host=database.host,
            dbname=database.dbname,
            user=database.user,
            password=database.password,
            port=database.port,
            sslmode=database.sslmode,
            connect_timeout=10,
        ) as connection,
        connection.cursor() as cursor,
    ):
        cursor.execute(
            sql.SQL(
                "SELECT d.date, d.hour, d.consumption, d.price, d.cost_per_hour "
                "FROM {invoice} i JOIN {detail} d ON d.id_invoice = i.id "
                "WHERE i.id_client = %s AND d.date >= %s AND d.date <= %s "
                "ORDER BY d.date, d.hour"
            ).format(**tables),
            (client_id, initial_date, final_date),
        )
        details = cursor.fetchall()
    if not details or {row[0] for row in details} != {
        initial_date.fromordinal(ordinal)
        for ordinal in range(initial_date.toordinal(), final_date.toordinal() + 1)
    }:
        return None
    return PreviousPeriod(calculate_bill_analysis(details, initial_date, final_date))


def _previous_invoice_summary(
    database: DatabaseSettings, client_id: int, current_initial_date: date
) -> PreviousInvoice | None:
    """Return the invoice ending most recently before the current invoice starts."""
    tables = {name: sql.Identifier(database.schema, name) for name in ("invoice", "detail")}
    with (
        psycopg.connect(
            host=database.host,
            dbname=database.dbname,
            user=database.user,
            password=database.password,
            port=database.port,
            sslmode=database.sslmode,
            connect_timeout=10,
        ) as connection,
        connection.cursor() as cursor,
    ):
        cursor.execute(
            sql.SQL(
                "SELECT i.id, i.initial_date, i.final_date "
                "FROM {invoice} i "
                "WHERE i.id_client = %s AND i.final_date < %s "
                "ORDER BY i.final_date DESC, i.initial_date DESC LIMIT 1"
            ).format(**tables),
            (client_id, current_initial_date),
        )
        row = cursor.fetchone()
        if row is not None:
            previous_id = row[0]
            cursor.execute(
                sql.SQL(
                    "SELECT d.date, d.hour, d.consumption, d.price, d.cost_per_hour "
                    "FROM {detail} d WHERE d.id_invoice = %s ORDER BY d.date, d.hour"
                ).format(detail=tables["detail"]),
                (previous_id,),
            )
            details = cursor.fetchall()
    if row is None:
        return None
    invoice_id, initial_date, final_date = row
    return PreviousInvoice(
        invoice_id=invoice_id,
        initial_date=initial_date,
        final_date=final_date,
        analysis=calculate_bill_analysis(details, initial_date, final_date),
    )


def _one_year_earlier(value: date) -> date:
    """Shift a date back one calendar year, including leap-day periods."""
    year = value.year - 1
    return date(year, value.month, min(value.day, monthrange(year, value.month)[1]))


def _report_message(report: InvoiceReport, sender: str) -> EmailMessage:
    """Build the plain-text initial version of an invoice summary email."""
    consumption = report.consumption_kwh.quantize(Decimal("1"), rounding=ROUND_HALF_UP)
    cost = report.cost_eur.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    daily_consumption = _whole_kwh(report.analysis.kwh_per_day)
    daily_cost = report.analysis.eur_per_day.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    effective_price = _money_per_kwh(report.analysis.average_price_eur_per_kwh)
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
        f"- {label}: {_whole_kwh(amount)} kWh "
        f"({_consumption_percentage(amount, report.consumption_kwh)}%)"
        for label, amount, _color in bands
    )
    comparison_text, comparison_html, comparison_bands = _previous_period_content(report, bands)
    previous_invoice_text, previous_invoice_html = _previous_invoice_content(report)
    daily_text, daily_html = _daily_content(report.analysis)
    savings_text, savings_html = _savings_content(report.savings)
    recommendation_text, recommendation_html = _recommendation_content(report.recommendations)
    insights_text = "\n".join(f"- {item}" for item in report.diagnostics)
    insights_html = "".join(f"<li>{escape(item)}</li>" for item in report.diagnostics)
    summary_html = "<br>".join(escape(item) for item in report.insights)
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
        f"Consum mitja diaria: {daily_consumption} kWh/dia\n"
        f"Cost mitja diaria: {daily_cost} EUR/dia\n"
        f"Preu mitja: {effective_price} EUR/kWh\n\n"
        "RESUM\n"
        f"{chr(10).join(report.insights)}\n\n"
        "PER QUÈ HA TINGUT AQUEST COST?\n"
        f"{insights_text}\n"
        f"{previous_invoice_text}"
        f"Cost mitjà diari: {daily_cost} EUR/dia\n"
        "\nDistribució del consum segons el preu:\n"
        f"{distribution}\n"
        f"{daily_text}"
        f"{comparison_text}"
        f"{savings_text}{recommendation_text}"
    )
    legend = "".join(
        "<li>"
        f'<span style="color: {color};font-size:20px;line-height:1;">&#9679;</span> '
        f"{escape(label)}: "
        f"{_whole_kwh(amount)} kWh "
        f"({_consumption_percentage(amount, report.consumption_kwh)}%)"
        "</li>"
        for label, amount, color in bands
    )
    message.add_alternative(
        '<html><body><div style="font-family:Arial,sans-serif;color:#263238;font-size:16px;line-height:1.5;max-width:680px;margin:auto;">'
        '<h1 style="font-size:25px;margin-bottom:4px;">Factura de la llum</h1>'
        f'<p style="font-size:18px;margin-top:0;">{report.initial_date:%d/%m/%Y} - {report.final_date:%d/%m/%Y}</p>'
        f'<p style="font-size:36px;font-weight:bold;margin:8px 0 0;">{cost} EUR</p>'
        f"<p>{consumption} kWh &nbsp;|&nbsp; {daily_cost} EUR/dia"
        f" &nbsp;|&nbsp; {effective_price} EUR/kWh</p>"
        f'<div style="background:#f1f8e9;padding:14px;border-radius:6px;">{summary_html}</div>'
        "<h2>Resum de la factura</h2>"
        f"<p><strong>Període:</strong> {report.initial_date:%d/%m/%Y} - "
        f"{report.final_date:%d/%m/%Y}<br>"
        f"<strong>Tarifa:</strong> {escape(report.tariff)}<br>"
        f"<strong>Consum total:</strong> {consumption} kWh<br>"
        f"<strong>Cost total:</strong> {cost} EUR<br>"
        f"<strong>Cost mitjà diari:</strong> {daily_cost} EUR/dia</p>"
        "<h3>Distribució del consum segons el preu</h3>"
        f'<img src="cid:{chart_cid}" alt="Gràfic de sectors del consum per preu" '
        'width="280" height="280">'
        f'<ul style="list-style:none;padding-left:0;margin-left:0;">{legend}</ul>'
        f"{daily_html}"
        "<h2>Per què ha tingut aquest cost?</h2>"
        f"<ul>{insights_html}</ul>"
        f"{previous_invoice_html}{comparison_html}{savings_html}{recommendation_html}"
        "</div></body></html>",
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
            _price_band_comparison_png(
                [(amount, color) for _label, amount, color in comparison_bands]
            ),
            maintype="image",
            subtype="png",
            cid=f"<{comparison_chart_cid}>",
            filename="comparativa-consum-per-preu.png",
            disposition="inline",
        )
    return message


def _previous_invoice_content(report: InvoiceReport) -> tuple[str, str]:
    """Build the optional daily-use comparison with the immediately prior invoice."""
    previous = report.previous_invoice
    if previous is None:
        return "", ""
    current_kwh_day, current_eur_day = _daily_averages(
        report.consumption_kwh,
        report.cost_eur,
        report.initial_date,
        report.final_date,
    )
    previous_kwh_day, previous_eur_day = _daily_averages(
        previous.analysis.total_kwh,
        previous.analysis.total_cost_eur,
        previous.initial_date,
        previous.final_date,
    )
    changes = report.comparison
    consumption_sentence = _daily_comparison_sentence(
        "Has consumit", current_kwh_day, previous_kwh_day, "kWh al dia",
        changes.consumption_per_day_change_percent,
    )
    cost_sentence = _daily_comparison_sentence(
        "Has pagat", current_eur_day, previous_eur_day, "EUR al dia",
        changes.cost_per_day_change_percent,
    )
    text = (
        "\nComparació amb la factura anterior:\n"
        f"{consumption_sentence}\n"
        f"{cost_sentence}\n"
    )
    html = (
        "<h3>Comparació amb la factura anterior</h3>"
        f"<ul><li>{consumption_sentence}</li>"
        f"<li>{cost_sentence}</li>"
        "</ul>"
    )
    return text, html


def _daily_comparison_sentence(
    subject: str,
    current: str,
    previous: str,
    unit: str,
    change: Decimal | None,
) -> str:
    if change is None:
        return "No hi ha prou dades per comparar les dues factures."
    if change > 0:
        comparison = "més cada dia"
    elif change < 0:
        comparison = "menys cada dia"
    else:
        comparison = "el mateix cada dia"
    return (
        f"{subject} {comparison}: {current} {unit}; "
        f"a la factura anterior eren {previous} {unit}."
    )


def _money_per_kwh(value: Decimal | None) -> str:
    return (
        "N/D"
        if value is None
        else f"{value.quantize(Decimal('0.001'), rounding=ROUND_HALF_UP):.3f}"
    )


def _change_label(value: Decimal | None) -> str:
    if value is None:
        return "no es pot calcular"
    arrow = "↑" if value > 0 else "↓" if value < 0 else "→"
    return f"{arrow} {abs(value):.1f}%"


def _year_change_sentence(subject: str, value: Decimal | None) -> str:
    if value is None:
        return f"{subject}: el canvi respecte del mateix període de l'any anterior no es pot calcular."
    if value > 0:
        verb = "ha augmentat"
    elif value < 0:
        verb = "ha disminuït"
    else:
        return f"{subject} s'ha mantingut igual que en el mateix període de l'any anterior."
    return (
        f"{subject} {verb} un {abs(value):.0f}% respecte del mateix període de l'any anterior."
    )


def _signed_eur(value: Decimal | None) -> str:
    return "N/D" if value is None else f"{value:+.2f}"


def _decomposition_html(comparison: BillComparison) -> str:
    consumption = comparison.consumption_effect_eur_per_day
    price = comparison.price_effect_eur_per_day
    if consumption is None or price is None:
        return ""
    return (
        "<li>Canvi estimat del cost diari atribuït al consum: "
        f"{consumption:+.2f} EUR/dia; al preu: {price:+.2f} EUR/dia.</li>"
    )


def _hourly_content(analysis: BillAnalysis) -> tuple[str, str]:
    """Render an email-safe hourly average profile with descriptive labels."""
    values = analysis.hourly_usage
    max_value = max((item.average_kwh_per_day for item in values), default=Decimal("0"))
    bars = []
    for item in values:
        width = int(item.average_kwh_per_day / max_value * 100) if max_value else 0
        color = (
            "#E53935"
            if item.average_price_eur_per_kwh is not None
            and item.average_price_eur_per_kwh >= EXPENSIVE_PRICE_THRESHOLD_EUR_PER_KWH
            else "#43A047"
        )
        bars.append(
            f'<tr><td style="width:100px;white-space:nowrap;">{item.hour:02d}:00–'
            f'{(item.hour + 1) % 24:02d}:00</td><td style="width:100%;">'
            f'<div style="height:16px;background:{color};width:{width}%;min-width:2px;">&nbsp;</div>'
            f'</td><td style="white-space:nowrap;padding-left:8px;">{_whole_kwh(item.average_kwh_per_day)} kWh/dia</td></tr>'
        )
    observations = []
    if analysis.highest_hour is not None:
        hour = analysis.highest_hour.hour
        observations.append(f"Màxim: {hour:02d}:00–{(hour + 1) % 24:02d}:00.")
    if analysis.lowest_hour is not None:
        hour = analysis.lowest_hour.hour
        observations.append(f"Mínim: {hour:02d}:00–{(hour + 1) % 24:02d}:00.")
    if analysis.expensive_hours:
        hours = ", ".join(f"{item.hour:02d}:00" for item in analysis.expensive_hours[:3])
        observations.append(f"Consum elevat en hores cares: {hours}.")
    text = "\nConsum mitjà per hora i dia (kWh/dia):\n" + "\n".join(
        f"{item.hour:02d}:00–{(item.hour + 1) % 24:02d}:00: {_whole_kwh(item.average_kwh_per_day)} kWh/dia"
        for item in values
    )
    text += "\n" + "\n".join(observations) + "\n"
    html = (
        "<p>Mitjana diària per hora (kWh/dia). Vermell indica una hora amb preu mitjà car; "
        'el text també identifica cada hora.</p><table role="presentation" '
        'style="width:100%;border-collapse:collapse;font-size:14px;">'
        + "".join(bars)
        + "</table><p>"
        + " ".join(escape(item) for item in observations)
        + "</p>"
    )
    return text, html


def _daily_content(analysis: BillAnalysis) -> tuple[str, str]:
    top_days = analysis.top_days
    text = "\nTop 3 dies per consum:\n" + "\n".join(
        f"{item.day:%d/%m/%Y} - {item.kwh:.0f} kWh"
        for item in top_days
    )
    text += f"\nMitjana: {_whole_kwh(analysis.kwh_per_day)} kWh/dia.\n"
    podium_items = ((1, "#c0c0c0", 66), (0, "#f9a825", 88), (2, "#b87333", 48))
    podium_cells = []
    for index, color, height in podium_items:
        if index >= len(top_days):
            podium_cells.append('<td style="width:33%;"></td>')
            continue
        item = top_days[index]
        podium_cells.append(
            '<td style="width:33%;text-align:center;vertical-align:bottom;padding:8px 4px;">'
            f'<div style="font-size:14px;">{item.day:%d/%m/%Y}</div>'
            f'<div style="font-size:16px;font-weight:bold;margin:4px 0;">{item.kwh:.0f} kWh</div>'
            f'<div style="height:{height}px;background:{color};color:#263238;'
            'font-size:20px;font-weight:bold;padding-top:8px;box-sizing:border-box;">'
            f'{index + 1}</div></td>'
        )
    html = (
        "<h3>Top 3 dies per consum</h3>"
        '<table role="presentation" style="width:100%;border-collapse:collapse;">'
        f"<tr>{''.join(podium_cells)}</tr></table>"
        f"<p>Mitjana del per\u00edode: {_whole_kwh(analysis.kwh_per_day)} kWh/dia.</p>"
    )
    return text, html


def _whole_kwh(value: Decimal) -> int:
    """Round consumption for display as a whole number of kWh."""
    return int(value.quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def _savings_content(savings: SavingsEstimate | None) -> tuple[str, str]:
    if savings is None:
        return "", ""
    percent = savings.movable_percentage * Decimal("100")
    text = (
        f"\nEstalvi potencial estimat: {savings.estimated_eur:.2f} EUR.\n"
        f"Estimació assumint que es podria moure el {percent:.0f}% del consum en hores cares "
        "a hores barates, valorades al preu mitjà observat en hores barates.\n"
    )
    html = (
        "<h2>Estalvi potencial</h2>"
        f"<p><strong>{savings.estimated_eur:.2f} EUR</strong></p>"
        f'<p style="font-size:13px;color:#546e7a;">Estimació assumint que es pot moure el '
        f"{percent:.0f}% del consum en hores cares a hores barates, amb el preu mitjà barat "
        "observat en aquesta factura. No és un estalvi garantit.</p>"
    )
    return text, html


def _recommendation_content(recommendations: tuple[str, ...]) -> tuple[str, str]:
    if not recommendations:
        return "", ""
    text = "\nConsell per a la propera factura:\n" + "\n".join(
        f"- {item}" for item in recommendations
    )
    html = (
        "<h2>Consell per a la propera factura</h2><ul>"
        + "".join(f"<li>{escape(item)}</li>" for item in recommendations)
        + "</ul>"
    )
    return text, html


def _daily_averages(
    consumption_kwh: Decimal,
    cost_eur: Decimal,
    initial_date: date,
    final_date: date,
) -> tuple[Decimal, Decimal]:
    """Return average kWh/day and EUR/day for an inclusive date range."""
    days = (final_date - initial_date).days + 1
    return (
        (consumption_kwh / days).quantize(Decimal("1"), rounding=ROUND_HALF_UP),
        (cost_eur / days).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP),
    )


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
            previous.analysis.distribution.cheap_kwh,
            CHEAP_PRICE_COLOR,
        ),
        (
            f"Preu mitjà (> {CHEAP_PRICE_THRESHOLD_EUR_PER_KWH} i < "
            f"{EXPENSIVE_PRICE_THRESHOLD_EUR_PER_KWH} EUR/kWh)",
            previous.analysis.distribution.medium_kwh,
            MEDIUM_PRICE_COLOR,
        ),
        (
            f"Preu alt (≥ {EXPENSIVE_PRICE_THRESHOLD_EUR_PER_KWH} EUR/kWh)",
            previous.analysis.distribution.expensive_kwh,
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
        f"- {label}: {_whole_kwh(amount)} kWh"
        for label, amount, _color in comparison_bands
    )
    comparison_legend = "".join(
        "<li>"
        f'<span style="color: {color};font-size:20px;line-height:1;">&#9679;</span> {label}: '
        f"{_whole_kwh(amount)} kWh"
        "</li>"
        for label, amount, color in comparison_bands
    )
    comparison_chart_cid = "comparativa-consum-per-preu"
    year_comparison = calculate_bill_comparison(report.analysis, previous.analysis)
    year_avg_price_sentence = _year_change_sentence(
        "El preu mitjà de l'electricitat", year_comparison.average_price_change_percent
    )
    text = (
        "\nComparativa amb el mateix període de l'any anterior:\n"
        f"{_year_change_sentence('El consum', calculate_percentage_change(report.analysis.total_kwh, previous.analysis.total_kwh))}\n"
        f"{_year_change_sentence('El cost', calculate_percentage_change(report.analysis.total_cost_eur, previous.analysis.total_cost_eur))}\n"
        f"{year_avg_price_sentence}\n"
        "Comparativa del consum per franja de preu:\n"
        f"{comparison_text}\n"
    )
    html = (
        "<h2>Comparativa amb el mateix període de l'any anterior</h2>"
        "<ul>"
        f"<li>{_year_change_sentence('El consum', calculate_percentage_change(report.analysis.total_kwh, previous.analysis.total_kwh))}</li>"
        f"<li>{_year_change_sentence('El cost', calculate_percentage_change(report.analysis.total_cost_eur, previous.analysis.total_cost_eur))}</li>"
        f"<li>{year_avg_price_sentence}</li>"
        "</ul><h3>Consum per franja de preu</h3>"
        f'<img src="cid:{comparison_chart_cid}" '
        'alt="Gràfic de columnes del consum per franja de preu i període" '
        'width="360" height="280">'
        f'<ul style="list-style:none;padding-left:0;margin-left:0;">{comparison_legend}</ul>'
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
    rgb_bands = [
        (_hex_to_rgb(color), amount / total if total else Decimal(0)) for amount, color in bands
    ]
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
    invoice_id: int | None = None,
) -> InvoiceReport:
    """Send a consumption and cost summary for the latest or selected invoice."""
    report = latest_invoice_report(database, client_id, invoice_id)
    with smtplib.SMTP_SSL(email.host, email.port, timeout=30) as smtp:
        smtp.login(email.sender, email.password)
        smtp.send_message(_report_message(report, email.sender))
    return report
