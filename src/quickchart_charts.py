"""Create attachment-free email chart URLs from aggregate report values.

QuickChart renders these URLs when an email client requests its remote images.
Only the values and dates visible in the charts are included in the URL.
"""

import json
from collections.abc import Sequence
from decimal import Decimal
from math import isfinite
from urllib.parse import urlencode

from src.report_calculations import DailyUsage, PriceDistribution
from src.report_charts import BAND_COLORS, BAND_LABELS, PREVIOUS_BAND_COLORS, format_kwh

QUICKCHART_URL = "https://quickchart.io/chart"
INK = "#223247"
MUTED = "#68788C"
GRID = "#E5EBF2"
BACKGROUND = "#F4F7FB"


def _values(distribution: PriceDistribution) -> list[float]:
    values = [
        float(distribution.cheap_kwh),
        float(distribution.medium_kwh),
        float(distribution.expensive_kwh),
    ]
    if any(not isfinite(value) or value < 0 for value in values):
        raise ValueError("Chart consumption must be finite and non-negative")
    return values


def _options(title: str, subtitle: str) -> dict:
    return {
        "responsive": False,
        "animation": False,
        "layout": {"padding": {"top": 18, "right": 25, "bottom": 12, "left": 25}},
        "plugins": {
            "title": {
                "display": True,
                "text": title,
                "align": "start",
                "color": INK,
                "font": {"family": "Arial", "size": 23, "weight": "bold"},
                "padding": {"bottom": 5},
            },
            "subtitle": {
                "display": True,
                "text": subtitle,
                "align": "start",
                "color": MUTED,
                "font": {"family": "Arial", "size": 13},
                "padding": {"bottom": 22},
            },
            "legend": {"display": False},
            "datalabels": {"display": False},
        },
    }


def _url(config: dict) -> str:
    query = urlencode(
        {
            "version": "4",
            "format": "png",
            "backgroundColor": BACKGROUND,
            "width": "648",
            "height": "420",
            "devicePixelRatio": "2",
            "c": json.dumps(config, ensure_ascii=False, separators=(",", ":")),
        }
    )
    return f"{QUICKCHART_URL}?{query}"


def price_pie_url(distribution: PriceDistribution) -> str:
    """Render a proportional pie, with the amounts and shares in its legend."""
    values = _values(distribution)
    total = sum(values)
    if total:
        labels = [
            f"{name} · {format_kwh(amount)} kWh · {amount / total:.0%}"
            for name, amount in zip(BAND_LABELS, values, strict=True)
        ]
        colors = list(BAND_COLORS)
    else:
        values = [1]
        labels = ["Sense consum registrat"]
        colors = [GRID]
    options = _options("On va el teu consum?", "Distribució per franja de preu")
    options["plugins"]["legend"] = {
        "display": True,
        "position": "right",
        "align": "center",
        "labels": {
            "color": INK,
            "boxWidth": 15,
            "boxHeight": 15,
            "padding": 18,
            "font": {"family": "Arial", "size": 13},
        },
    }
    config = {
        "type": "pie",
        "data": {
            "labels": labels,
            "datasets": [
                {
                    "data": values,
                    "backgroundColor": colors,
                    "borderColor": "#FFFFFF",
                    "borderWidth": 4,
                    "hoverOffset": 0,
                }
            ],
        },
        "options": options,
    }
    return _url(config)


def daily_podium_url(days: Sequence[DailyUsage], daily_average: Decimal) -> str:
    """Put the highest day in the center, using actual kWh for bar heights."""
    if any(not item.kwh.is_finite() or item.kwh < 0 for item in days):
        raise ValueError("Chart consumption must be finite and non-negative")
    ranked = sorted(days, key=lambda item: (-item.kwh, item.day))[:3]
    ordered = [ranked[index] if index < len(ranked) else None for index in (1, 0, 2)]
    labels = []
    for item in ordered:
        if item is None:
            labels.append("")
            continue
        place = 1 + sum(other.kwh > item.kwh for other in ranked)
        labels.append([f"{place} · {item.day:%d/%m/%Y}", f"{format_kwh(item.kwh)} kWh"])
    options = _options(
        "Els dies amb més consum",
        f"Top 3 del període · mitjana {format_kwh(daily_average)} kWh/dia",
    )
    options["plugins"]["datalabels"] = {
        "display": False,
    }
    options["scales"] = {
        "x": {
            "grid": {"display": False},
            "border": {"display": False},
            "ticks": {"color": INK, "font": {"family": "Arial", "size": 14, "weight": "bold"}},
        },
        "y": {
            "beginAtZero": True,
            "suggestedMax": max((float(day.kwh) for day in ranked), default=0) * 1.22 or 1,
            "grid": {"color": GRID},
            "border": {"display": False, "dash": [4, 4]},
            "ticks": {"color": MUTED, "maxTicksLimit": 5},
            "title": {"display": True, "text": "kWh", "color": MUTED},
        },
    }
    config = {
        "type": "bar",
        "data": {
            "labels": labels if ranked else ["", "Sense dades diàries", ""],
            "datasets": [
                {
                    "data": [float(day.kwh) if day is not None else 0 for day in ordered],
                    "backgroundColor": ["#A9BACD", "#E8B34D", "#C68D6D"],
                    "borderRadius": 13,
                    "barPercentage": 0.65,
                    "categoryPercentage": 0.75,
                }
            ],
        },
        "options": options,
    }
    return _url(config)


def price_columns_url(current: PriceDistribution, previous: PriceDistribution | None = None) -> str:
    """Show each price band on a shared zero-based scale."""
    values = _values(current)
    prior = _values(previous) if previous is not None else None
    options = _options(
        "El consum, franja a franja",
        "Comparativa amb el mateix període de l'any anterior"
        if prior is not None
        else "Període actual",
    )
    options["plugins"]["legend"] = {
        "display": prior is not None,
        "position": "top",
        "labels": {"color": MUTED, "font": {"family": "Arial", "size": 12}},
    }
    options["plugins"]["datalabels"] = {
        "display": True,
        "anchor": "end",
        "align": "end",
        "offset": 3,
        "color": INK,
        "font": {"family": "Arial", "size": 11, "weight": "bold"},
    }
    options["scales"] = {
        "x": {
            "grid": {"display": False},
            "border": {"color": GRID},
            "ticks": {"color": INK, "font": {"family": "Arial", "size": 13}},
        },
        "y": {
            "beginAtZero": True,
            "suggestedMax": max(values + (prior or [0])) * 1.2 or 1,
            "grid": {"color": GRID},
            "border": {"display": False},
            "ticks": {"color": MUTED, "maxTicksLimit": 5},
            "title": {"display": True, "text": "kWh", "color": MUTED},
        },
    }
    datasets = [
        {
            "label": "Període actual",
            "data": values,
            "backgroundColor": list(BAND_COLORS),
            "borderRadius": 9,
            "barPercentage": 0.82,
            "categoryPercentage": 0.72,
        }
    ]
    if prior is not None:
        datasets.append(
            {
                "label": "Mateix període, any anterior",
                "data": prior,
                "backgroundColor": list(PREVIOUS_BAND_COLORS),
                "borderRadius": 9,
                "barPercentage": 0.82,
                "categoryPercentage": 0.72,
            }
        )
    return _url(
        {"type": "bar", "data": {"labels": BAND_LABELS, "datasets": datasets}, "options": options}
    )
