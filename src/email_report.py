"""Send invoice-report emails without changing invoice data."""

import smtplib
from calendar import monthrange
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date
from decimal import ROUND_HALF_UP, Decimal
from email.message import EmailMessage
from html import escape
from secrets import token_urlsafe

import psycopg
from psycopg import sql

from src.config import DatabaseSettings, EmailSettings
from src.quickchart_charts import daily_podium_url, price_columns_url, price_pie_url
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
from src.report_charts import (
    BAND_COLORS,
    PREVIOUS_BAND_COLORS,
    daily_podium_png,
    format_kwh,
    price_columns_png,
    price_pie_png,
)

CHEAP_PRICE_THRESHOLD_EUR_PER_KWH = Decimal("0.15")
EXPENSIVE_PRICE_THRESHOLD_EUR_PER_KWH = Decimal("0.25")
CHEAP_PRICE_COLOR, MEDIUM_PRICE_COLOR, EXPENSIVE_PRICE_COLOR = BAND_COLORS


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


def _report_message(
    report: InvoiceReport, sender: str, chart_urls: Mapping[str, str]
) -> EmailMessage:
    """Build text and HTML alternatives with linked charts and no MIME image parts."""
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
    comparison_text, comparison_html, _comparison_bands = _previous_period_content(report, bands)
    previous_invoice_text, previous_invoice_html = _previous_invoice_content(report)
    daily_text = _daily_content(report.analysis)
    savings_text, savings_html = _savings_content(report.savings)
    recommendation_text, recommendation_html = _recommendation_content(report.recommendations)
    insights_text = "\n".join(f"- {item}" for item in report.diagnostics)
    insights_html = "".join(f"<li>{escape(item)}</li>" for item in report.diagnostics)
    summary_html = "<br>".join(escape(item) for item in report.insights)
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
        f"Consum mitj\u00e0 diari: {daily_consumption} kWh/dia\n"
        f"Cost total: {cost} EUR\n"
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
        f"<strong>Consum mitj\u00e0 diari:</strong> {daily_consumption} kWh/dia<br>"
        f"<strong>Cost total:</strong> {cost} EUR<br>"
        f"<strong>Cost mitjà diari:</strong> {daily_cost} EUR/dia</p>"
        f'{_side_by_side_charts_html(chart_urls["pie"], chart_urls["podium"])}'
        "<h2>Per què ha tingut aquest cost?</h2>"
        f"<ul>{insights_html}</ul>"
        f"{previous_invoice_html}{comparison_html}"
        f'{_chart_html(chart_urls["columns"], "Consum per franja de preu en kWh")}'
        f"{savings_html}{recommendation_html}"
        "</div></body></html>",
        subtype="html",
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
        f"Factura actual ({report.initial_date:%d/%m/%Y} - {report.final_date:%d/%m/%Y}): "
        f"{current_kwh_day} kWh/dia\n"
        f"Factura anterior ({previous.initial_date:%d/%m/%Y} - {previous.final_date:%d/%m/%Y}): "
        f"{previous_kwh_day} kWh/dia\n"
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


def _daily_content(analysis: BillAnalysis) -> str:
    top_days = analysis.top_days
    text = "\nDies amb més consum (top 3):\n" + "\n".join(
        f"{item.day:%d/%m/%Y} - {format_kwh(item.kwh)} kWh"
        for item in top_days
    )
    return text


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
        ("Preu baix — període actual", current_bands[0][1], BAND_COLORS[0]),
        ("Preu baix — any anterior", bands[0][1], PREVIOUS_BAND_COLORS[0]),
        ("Preu mitjà — període actual", current_bands[1][1], BAND_COLORS[1]),
        ("Preu mitjà — any anterior", bands[1][1], PREVIOUS_BAND_COLORS[1]),
        ("Preu alt — període actual", current_bands[2][1], BAND_COLORS[2]),
        ("Preu alt — any anterior", bands[2][1], PREVIOUS_BAND_COLORS[2]),
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
        "</ul>"
        f'<ul style="list-style:none;padding-left:0;margin-left:0;">{comparison_legend}</ul>'
    )
    return text, html, comparison_bands


def _consumption_percentage(amount: Decimal, total: Decimal) -> Decimal:
    """Return a one-decimal consumption share without dividing by zero."""
    if not total:
        return Decimal("0.0")
    return (amount * Decimal("100") / total).quantize(Decimal("0.1"), rounding=ROUND_HALF_UP)


def _chart_html(url: str, alt: str) -> str:
    """Use table alignment for email clients and scale down on narrow screens."""
    return (
        '<table role="presentation" width="100%" cellpadding="0" cellspacing="0">'
        '<tr><td align="center" style="text-align:center;padding:12px 0;">'
        f'<img src="{escape(url, quote=True)}" alt="{escape(alt, quote=True)}" '
        'width="648" style="display:block;width:100%;max-width:648px;height:auto;'
        'margin:0 auto;border:0;"></td></tr></table>'
    )


def _side_by_side_charts_html(pie_url: str, podium_url: str) -> str:
    """Place the distribution and top-days charts side by side."""
    return (
        '<table role="presentation" width="100%" cellpadding="0" cellspacing="0" '
        'style="table-layout:fixed;margin:12px 0;"><tr>'
        '<td width="50%" valign="top" style="width:50%;padding:6px;text-align:center;">'
        f'<img src="{escape(pie_url, quote=True)}" alt="" '
        'width="320" style="display:block;width:100%;max-width:320px;height:auto;margin:0 auto;border:0;">'
        '</td><td width="50%" valign="top" style="width:50%;padding:6px;text-align:center;">'
        f'<img src="{escape(podium_url, quote=True)}" alt="" '
        'width="320" style="display:block;width:100%;max-width:320px;height:auto;margin:0 auto;border:0;">'
        '</td></tr></table>'
    )


def _write_report_charts(report: InvoiceReport, email: EmailSettings) -> dict[str, str]:
    """Cache Matplotlib previews locally; link email to remote-rendered charts."""
    charts = {
        "pie": price_pie_png(report.analysis.distribution),
        "podium": daily_podium_png(report.analysis.top_days, report.analysis.kwh_per_day),
        "columns": price_columns_png(
            report.analysis.distribution,
            report.previous_period.analysis.distribution if report.previous_period else None,
        ),
    }
    # Random per-message paths reveal no invoice IDs or recipient information.
    key = token_urlsafe(24)
    directory = email.chart_dir / key
    directory.mkdir(parents=True, exist_ok=False)
    for name, data in charts.items():
        (directory / f"{name}.png").write_bytes(data)
    return {
        "pie": price_pie_url(report.analysis.distribution),
        "podium": daily_podium_url(report.analysis.top_days),
        "columns": price_columns_url(
            report.analysis.distribution,
            report.previous_period.analysis.distribution if report.previous_period else None,
        ),
    }


def send_invoice_report(
    database: DatabaseSettings,
    email: EmailSettings,
    client_id: int = 1,
    invoice_id: int | None = None,
) -> InvoiceReport:
    """Send a consumption and cost summary for the latest or selected invoice."""
    report = latest_invoice_report(database, client_id, invoice_id)
    chart_urls = _write_report_charts(report, email)
    message = _report_message(report, email.sender, chart_urls)
    with smtplib.SMTP_SSL(email.host, email.port, timeout=30) as smtp:
        smtp.login(email.sender, email.password)
        smtp.send_message(message)
    return report
