"""Pure calculations used by the invoice report."""

from collections import defaultdict
from dataclasses import dataclass
from datetime import date, time
from decimal import ROUND_HALF_UP, Decimal

CHEAP_PRICE_THRESHOLD_EUR_PER_KWH = Decimal("0.15")
EXPENSIVE_PRICE_THRESHOLD_EUR_PER_KWH = Decimal("0.25")
MOVABLE_CONSUMPTION_PERCENTAGE = Decimal("0.20")
ZERO = Decimal("0")
THOUSAND = Decimal("1000")


@dataclass(frozen=True)
class PriceDistribution:
    cheap_kwh: Decimal
    medium_kwh: Decimal
    expensive_kwh: Decimal
    cheap_percent: Decimal
    medium_percent: Decimal
    expensive_percent: Decimal


@dataclass(frozen=True)
class DailyUsage:
    day: date
    kwh: Decimal


@dataclass(frozen=True)
class HourUsage:
    hour: int
    average_kwh_per_day: Decimal
    average_price_eur_per_kwh: Decimal | None
    expensive_kwh_per_day: Decimal


@dataclass(frozen=True)
class BillAnalysis:
    total_kwh: Decimal
    total_cost_eur: Decimal
    days: int
    kwh_per_day: Decimal
    eur_per_day: Decimal
    average_price_eur_per_kwh: Decimal | None
    distribution: PriceDistribution
    daily_usage: tuple[DailyUsage, ...]
    hourly_usage: tuple[HourUsage, ...]
    top_days: tuple[DailyUsage, ...]
    highest_hour: HourUsage | None
    lowest_hour: HourUsage | None
    expensive_hours: tuple[HourUsage, ...]
    cheap_reference_price: Decimal | None


@dataclass(frozen=True)
class BillComparison:
    consumption_per_day_change_percent: Decimal | None
    cost_per_day_change_percent: Decimal | None
    average_price_change_percent: Decimal | None
    consumption_effect_eur_per_day: Decimal | None
    price_effect_eur_per_day: Decimal | None


@dataclass(frozen=True)
class SavingsEstimate:
    estimated_eur: Decimal
    movable_percentage: Decimal


def calculate_price_distribution(details: list[tuple]) -> PriceDistribution:
    """Group detail consumption in Wh into the three configured price bands."""
    bands = [ZERO, ZERO, ZERO]
    total = ZERO
    for _day, _hour, consumption_wh, price, _cost in details:
        kwh = Decimal(consumption_wh) / THOUSAND
        total += kwh
        if Decimal(price) <= CHEAP_PRICE_THRESHOLD_EUR_PER_KWH:
            bands[0] += kwh
        elif Decimal(price) < EXPENSIVE_PRICE_THRESHOLD_EUR_PER_KWH:
            bands[1] += kwh
        else:
            bands[2] += kwh
    percentages = tuple(_percent(amount, total) for amount in bands)
    return PriceDistribution(*bands, *percentages)


def calculate_bill_analysis(
    details: list[tuple], initial_date: date, final_date: date
) -> BillAnalysis:
    """Calculate consistent total, daily, hourly, and price-band bill metrics.

    Detail rows are `(date, hour, consumption_wh, price_eur_per_kwh, cost_eur)`.
    Daily averages use the inclusive invoice calendar period.
    """
    days = (final_date - initial_date).days + 1
    if days < 1:
        raise ValueError("Invoice final_date must not be before initial_date")
    total_wh = sum((Decimal(row[2]) for row in details), ZERO)
    total_kwh = total_wh / THOUSAND
    total_cost = sum((Decimal(row[4]) for row in details), ZERO)
    distribution = calculate_price_distribution(details)
    daily = defaultdict(lambda: ZERO)
    hourly_wh = [ZERO for _ in range(24)]
    hourly_cost = [ZERO for _ in range(24)]
    hourly_kwh = [ZERO for _ in range(24)]
    cheap_weighted_cost = ZERO
    cheap_wh = ZERO
    for day, hour_value, consumption_wh_value, price_value, cost_value in details:
        consumption_wh = Decimal(consumption_wh_value)
        price = Decimal(price_value)
        cost = Decimal(cost_value)
        hour = hour_value.hour if isinstance(hour_value, time) else int(hour_value)
        kwh = consumption_wh / THOUSAND
        daily[day] += kwh
        hourly_wh[hour] += consumption_wh
        hourly_kwh[hour] += kwh
        hourly_cost[hour] += cost
        if price <= CHEAP_PRICE_THRESHOLD_EUR_PER_KWH:
            cheap_weighted_cost += cost
            cheap_wh += consumption_wh
    daily_usage = tuple(DailyUsage(day, amount) for day, amount in sorted(daily.items()))
    average_price = total_cost / total_kwh if total_kwh else None
    hour_data = []
    for hour in range(24):
        amount = hourly_kwh[hour]
        avg_price = hourly_cost[hour] / amount if amount else None
        expensive = (
            amount
            if avg_price is not None and avg_price >= EXPENSIVE_PRICE_THRESHOLD_EUR_PER_KWH
            else ZERO
        )
        hour_data.append(
            HourUsage(
                hour,
                (amount / days).quantize(Decimal("0.001"), rounding=ROUND_HALF_UP),
                avg_price,
                (expensive / days).quantize(Decimal("0.001"), rounding=ROUND_HALF_UP),
            )
        )
    represented_hours = [hour for hour in hour_data if hourly_kwh[hour.hour] > 0]
    highest = max(represented_hours, key=lambda item: item.average_kwh_per_day, default=None)
    lowest = min(represented_hours, key=lambda item: item.average_kwh_per_day, default=None)
    mean_hour = total_kwh / (days * 24) if days else ZERO
    expensive_hours = tuple(
        hour
        for hour in hour_data
        if hour.average_price_eur_per_kwh is not None
        and hour.average_price_eur_per_kwh >= EXPENSIVE_PRICE_THRESHOLD_EUR_PER_KWH
        and hour.average_kwh_per_day > mean_hour
    )
    return BillAnalysis(
        total_kwh=total_kwh,
        total_cost_eur=total_cost,
        days=days,
        kwh_per_day=total_kwh / days,
        eur_per_day=total_cost / days,
        average_price_eur_per_kwh=average_price,
        distribution=distribution,
        daily_usage=daily_usage,
        hourly_usage=tuple(hour_data),
        top_days=tuple(sorted(daily_usage, key=lambda item: item.kwh, reverse=True)[:3]),
        highest_hour=highest,
        lowest_hour=lowest,
        expensive_hours=expensive_hours,
        cheap_reference_price=cheap_weighted_cost / (cheap_wh / THOUSAND) if cheap_wh else None,
    )


def calculate_percentage_change(
    current: Decimal | None, previous: Decimal | None
) -> Decimal | None:
    """Return signed percent change, or None when a baseline is missing or zero."""
    if current is None or previous is None or not previous:
        return None
    return ((current - previous) * Decimal("100") / previous).quantize(
        Decimal("0.1"), rounding=ROUND_HALF_UP
    )


def calculate_bill_comparison(
    current: BillAnalysis, previous: BillAnalysis | None
) -> BillComparison:
    """Compare daily use/cost and decompose daily cost change symmetrically.

    The Shapley-style split averages the two counterfactual orders: consumption is
    valued at the midpoint of the two effective prices; price at midpoint use.
    It explains normalized EUR/day change, not the exact change in invoice totals.
    """
    if previous is None:
        return BillComparison(None, None, None, None, None)
    consumption_change = calculate_percentage_change(current.kwh_per_day, previous.kwh_per_day)
    cost_change = calculate_percentage_change(current.eur_per_day, previous.eur_per_day)
    price_change = calculate_percentage_change(
        current.average_price_eur_per_kwh, previous.average_price_eur_per_kwh
    )
    if current.average_price_eur_per_kwh is None or previous.average_price_eur_per_kwh is None:
        consumption_effect = price_effect = None
    else:
        consumption_effect = (
            (current.kwh_per_day - previous.kwh_per_day)
            * (current.average_price_eur_per_kwh + previous.average_price_eur_per_kwh)
            / Decimal("2")
        )
        price_effect = (
            (current.average_price_eur_per_kwh - previous.average_price_eur_per_kwh)
            * (current.kwh_per_day + previous.kwh_per_day)
            / Decimal("2")
        )
    return BillComparison(
        consumption_change,
        cost_change,
        price_change,
        consumption_effect,
        price_effect,
    )


def estimate_potential_savings(
    details: list[tuple],
    movable_percentage: Decimal = MOVABLE_CONSUMPTION_PERCENTAGE,
) -> SavingsEstimate | None:
    """Estimate shifting a share of high-price use to its measured cheap-price average.

    Returns None unless both expensive consumption and a cheap reference price exist.
    """
    if not ZERO <= movable_percentage <= Decimal("1"):
        raise ValueError("movable_percentage must be between 0 and 1")
    cheap_rows = [row for row in details if Decimal(row[3]) <= CHEAP_PRICE_THRESHOLD_EUR_PER_KWH]
    expensive_rows = [
        row for row in details if Decimal(row[3]) >= EXPENSIVE_PRICE_THRESHOLD_EUR_PER_KWH
    ]
    cheap_kwh = sum((Decimal(row[2]) / THOUSAND for row in cheap_rows), ZERO)
    if not cheap_kwh or not expensive_rows or not movable_percentage:
        return None
    cheap_cost = sum((Decimal(row[4]) for row in cheap_rows), ZERO)
    reference_price = cheap_cost / cheap_kwh
    savings = sum(
        (Decimal(row[2]) / THOUSAND)
        * movable_percentage
        * max(Decimal(row[3]) - reference_price, ZERO)
        for row in expensive_rows
    )
    return SavingsEstimate(
        savings.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP), movable_percentage
    )


def generate_bill_insights(
    current: BillAnalysis,
    comparison: BillComparison,
    savings: SavingsEstimate | None,
) -> tuple[str, ...]:
    """Generate data-supported, plain-language Catalan summary and diagnostic points."""
    summary = [f"Aquesta factura és de {current.total_cost_eur:.2f} EUR."]
    if (
        comparison.consumption_per_day_change_percent is not None
        and abs(comparison.consumption_per_day_change_percent) >= Decimal("1")
    ):
        summary.append(
            f"El consum diari ha {('augmentat' if comparison.consumption_per_day_change_percent > 0 else 'baixat' if comparison.consumption_per_day_change_percent < 0 else 'quedat igual')} "
            f"un {abs(comparison.consumption_per_day_change_percent):.0f}% respecte de la factura anterior."
        )
    if (
        comparison.cost_per_day_change_percent is not None
        and abs(comparison.cost_per_day_change_percent) >= Decimal("1")
    ):
        summary.append(
            f"El cost diari ha {('augmentat' if comparison.cost_per_day_change_percent > 0 else 'baixat' if comparison.cost_per_day_change_percent < 0 else 'quedat igual')} "
            f"un {abs(comparison.cost_per_day_change_percent):.0f}%."
        )
    if (
        comparison.consumption_per_day_change_percent is not None
        and comparison.cost_per_day_change_percent is not None
        and comparison.average_price_change_percent is not None
        and comparison.consumption_per_day_change_percent < 0
        and comparison.cost_per_day_change_percent > 0
        and abs(comparison.consumption_per_day_change_percent) >= Decimal("1")
        and abs(comparison.cost_per_day_change_percent) >= Decimal("1")
    ):
        summary.append("Tot i consumir menys cada dia, el preu mitjà de l'electricitat ha pujat.")
    elif savings is not None:
        summary.append("Part del consum s'ha fet en hores amb un preu més alt.")
    return tuple(summary[:4])


def generate_recommendations(
    analysis: BillAnalysis, savings: SavingsEstimate | None
) -> tuple[str, ...]:
    """Return at most two conservative recommendations supported by hourly data."""
    recommendations = []
    expensive_share = analysis.distribution.expensive_percent
    if analysis.expensive_hours:
        times = ", ".join(
            f"{hour.hour:02d}:00–{(hour.hour + 1) % 24:02d}:00"
            for hour in analysis.expensive_hours[:2]
        )
        recommendations.append(
            f"Hi ha força consum en hores cares ({times}). Si pots, programa aparells flexibles a una altra hora."
        )
    elif expensive_share >= Decimal("20"):
        recommendations.append(
            f"El {expensive_share:.1f}% del consum és en hores cares; revisa si algun consum flexible es pot moure."
        )
    elif analysis.distribution.cheap_percent >= Decimal("60"):
        recommendations.append(
            f"Ja concentres el {analysis.distribution.cheap_percent:.1f}% del consum en hores barates; el patró és favorable."
        )
    if savings is not None and savings.estimated_eur > 0 and len(recommendations) < 2:
        recommendations.append(
            f"Moure una part del consum car podria estalviar aproximadament {savings.estimated_eur:.2f} EUR, segons l'estimació indicada."
        )
    return tuple(recommendations[:2])


def generate_diagnostics(analysis: BillAnalysis, comparison: BillComparison) -> tuple[str, ...]:
    """Describe measurable reasons for the bill using its hourly and prior-bill data."""
    messages = [
        f"El {analysis.distribution.expensive_percent:.0f}% del consum s'ha fet a un preu car.",
        f"El {analysis.distribution.cheap_percent:.0f}% del consum s'ha fet a un preu barat.",
    ]
    if comparison.consumption_per_day_change_percent is not None:
        delta = comparison.consumption_per_day_change_percent
        direction = "més" if delta > 0 else "menys" if delta < 0 else "igual"
        messages.append(
            f"El consum diari ha estat {direction} que en la factura anterior "
            f"({abs(delta):.0f}%)."
        )
    if comparison.average_price_change_percent is not None:
        delta = comparison.average_price_change_percent
        if delta > 0:
            messages.append(
                f"El preu mitjà de l'electricitat ha pujat un {abs(delta):.0f}% "
                "respecte de la factura anterior."
            )
        elif delta < 0:
            messages.append(
                f"El preu mitjà de l'electricitat ha baixat un {abs(delta):.0f}% "
                "respecte de la factura anterior."
            )
        else:
            messages.append(
                "El preu mitjà de l'electricitat s'ha mantingut igual que en la factura anterior."
            )
    if analysis.expensive_hours:
        times = ", ".join(
            f"{hour.hour:02d}:00–{(hour.hour + 1) % 24:02d}:00"
            for hour in analysis.expensive_hours[:2]
        )
        messages.append(f"Hi ha consum elevat i preu car entre {times}.")
    return tuple(messages[:5])


def _percent(amount: Decimal, total: Decimal) -> Decimal:
    if not total:
        return ZERO.quantize(Decimal("0.1"))
    return (amount * Decimal("100") / total).quantize(Decimal("0.1"), rounding=ROUND_HALF_UP)
