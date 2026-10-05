from datetime import date, time
from decimal import Decimal

import pytest

from src.report_calculations import (
    calculate_bill_analysis,
    calculate_bill_comparison,
    calculate_percentage_change,
    calculate_price_distribution,
    estimate_potential_savings,
    generate_bill_insights,
    generate_recommendations,
)


def detail(day, hour, wh, price, cost):
    return (date.fromisoformat(day), time(hour), Decimal(wh), Decimal(price), Decimal(cost))


def test_price_distribution_includes_threshold_boundaries():
    details = [
        detail("2026-01-01", 0, "1000", "0.15", "0.15"),
        detail("2026-01-01", 1, "1000", "0.15001", "0.15001"),
        detail("2026-01-01", 2, "1000", "0.25", "0.25"),
    ]

    result = calculate_price_distribution(details)

    assert (result.cheap_kwh, result.medium_kwh, result.expensive_kwh) == (
        Decimal("1"),
        Decimal("1"),
        Decimal("1"),
    )
    assert (result.cheap_percent, result.medium_percent, result.expensive_percent) == (
        Decimal("33.3"),
        Decimal("33.3"),
        Decimal("33.3"),
    )


def test_hourly_and_daily_aggregation_and_top_days():
    details = [
        detail("2026-01-01", 20, "2000", "0.30", "0.60"),
        detail("2026-01-01", 21, "1000", "0.20", "0.20"),
        detail("2026-01-02", 20, "4000", "0.30", "1.20"),
    ]

    result = calculate_bill_analysis(details, date(2026, 1, 1), date(2026, 1, 2))

    assert result.total_kwh == Decimal("7")
    assert result.kwh_per_day == Decimal("3.5")
    assert [(item.day.day, item.kwh) for item in result.top_days] == [
        (2, Decimal("4")),
        (1, Decimal("3")),
    ]
    assert result.hourly_usage[20].average_kwh_per_day == Decimal("3.000")
    assert result.hourly_usage[21].average_kwh_per_day == Decimal("0.500")
    assert result.highest_hour.hour == 20
    assert result.lowest_hour.hour == 21
    assert result.expensive_hours[0].hour == 20


def test_change_and_average_price_handle_different_period_lengths_and_zero():
    current = calculate_bill_analysis(
        [detail("2026-01-01", 0, "2000", "0.2", "0.4")],
        date(2026, 1, 1),
        date(2026, 1, 1),
    )
    previous = calculate_bill_analysis(
        [detail("2025-12-01", 0, "3000", "0.1", "0.3")],
        date(2025, 12, 1),
        date(2025, 12, 2),
    )

    comparison = calculate_bill_comparison(current, previous)
    assert current.average_price_eur_per_kwh == Decimal("0.2")
    assert comparison.consumption_per_day_change_percent == Decimal("33.3")
    assert comparison.cost_per_day_change_percent == Decimal("166.7")
    assert comparison.average_price_change_percent == Decimal("100.0")
    assert comparison.consumption_effect_eur_per_day + comparison.price_effect_eur_per_day == (
        current.eur_per_day - previous.eur_per_day
    )
    assert calculate_percentage_change(Decimal("1"), Decimal("0")) is None
    assert calculate_bill_comparison(current, None).cost_per_day_change_percent is None


def test_savings_uses_configured_fraction_and_observed_cheap_price():
    details = [
        detail("2026-01-01", 1, "1000", "0.10", "0.10"),
        detail("2026-01-01", 20, "5000", "0.30", "1.50"),
    ]

    result = estimate_potential_savings(details)

    assert result.estimated_eur == Decimal("0.20")
    assert result.movable_percentage == Decimal("0.20")
    assert estimate_potential_savings([details[1]]) is None
    with pytest.raises(ValueError):
        estimate_potential_savings(details, Decimal("1.1"))


def test_zero_consumption_and_recommendation_for_cheap_usage():
    empty = calculate_bill_analysis([], date(2026, 1, 1), date(2026, 1, 1))
    assert empty.average_price_eur_per_kwh is None
    assert empty.distribution.cheap_percent == Decimal("0.0")
    cheap = calculate_bill_analysis(
        [detail("2026-01-01", 3, "10000", "0.10", "1")],
        date(2026, 1, 1),
        date(2026, 1, 1),
    )
    assert generate_recommendations(cheap, None)


def test_insights_and_recommendations_use_measured_price_and_hour_patterns():
    current = calculate_bill_analysis(
        [
            detail("2026-01-01", 20, "8000", "0.30", "2.4"),
            detail("2026-01-01", 3, "2000", "0.10", "0.2"),
        ],
        date(2026, 1, 1),
        date(2026, 1, 1),
    )
    previous = calculate_bill_analysis(
        [detail("2025-12-01", 3, "12000", "0.10", "1.2")],
        date(2025, 12, 1),
        date(2025, 12, 1),
    )
    comparison = calculate_bill_comparison(current, previous)
    insights = generate_bill_insights(current, comparison, None)
    recommendations = generate_recommendations(current, None)

    assert any("augmentat" in message for message in insights)
    assert any("hores cares" in message for message in recommendations)
    assert len(recommendations) <= 2
