"""Create attachment-free email chart URLs from aggregate report values.

QuickChart renders these URLs when an email client requests its remote images.
Only the values and dates visible in the charts are included in the URL.
"""

import json
from collections.abc import Sequence
from decimal import ROUND_HALF_UP, Decimal
from math import isfinite
from urllib.parse import urlencode

from src.report_calculations import DailyUsage, PriceDistribution
from src.report_charts import BAND_COLORS, BAND_LABELS, PREVIOUS_BAND_COLORS, format_kwh

QUICKCHART_URL = "https://quickchart.io/chart"
PIE_PERCENT_FORMATTER = (
    "function(value,context){return Math.round(value/"
    "context.chart.data.datasets[0].data.reduce((sum,item)=>sum+item,0)*100)+'%';}"
)
PIE_PERCENT_FORMATTER_PLACEHOLDER = "__PIE_PERCENT_FORMATTER__"
INK = "#223247"
MUTED = "#68788C"
GRID = "#E5EBF2"


def _values(distribution: PriceDistribution) -> list[float]:
    values = [
        float(distribution.cheap_kwh),
        float(distribution.medium_kwh),
        float(distribution.expensive_kwh),
    ]
    if any(not isfinite(value) or value < 0 for value in values):
        raise ValueError("Chart consumption must be finite and non-negative")
    return values


def _options() -> dict:
    return {
        "responsive": False,
        "animation": False,
        "layout": {"padding": {"top": 8, "right": 25, "bottom": 12, "left": 25}},
        "plugins": {
            "legend": {"display": False},
            "datalabels": {"display": False},
        },
    }


def _url(config: dict, *, version: str = "4", height: int = 420) -> str:
    chart = json.dumps(config, ensure_ascii=False, separators=(",", ":"))
    chart = chart.replace(json.dumps(PIE_PERCENT_FORMATTER_PLACEHOLDER), PIE_PERCENT_FORMATTER)
    query = urlencode(
        {
            "version": version,
            "format": "png",
            "backgroundColor": "transparent",
            "width": "648",
            "height": str(height),
            "devicePixelRatio": "2",
            "c": chart,
        }
    )
    return f"{QUICKCHART_URL}?{query}"


def price_pie_url(distribution: PriceDistribution) -> str:
    """Render a doughnut with centered total consumption and no legend."""
    values = _values(distribution)
    total = sum(values)
    total_decimal = distribution.cheap_kwh + distribution.medium_kwh + distribution.expensive_kwh
    rounded_total = int(total_decimal.quantize(Decimal("1"), rounding=ROUND_HALF_UP))
    if total:
        labels = list(BAND_LABELS)
        colors = list(BAND_COLORS)
    else:
        values = [1]
        labels = ["Sense consum registrat"]
        colors = [GRID]
    options = _options()
    options["plugins"]["datalabels"] = {
        "display": bool(total),
        "anchor": "center",
        "align": "center",
        "color": "#FFFFFF",
        "font": {"family": "Arial", "size": 14, "weight": "bold"},
        "formatter": PIE_PERCENT_FORMATTER_PLACEHOLDER,
    }
    options["plugins"]["doughnutlabel"] = {
        "labels": [
            {"text": str(rounded_total), "color": INK, "font": {"size": 35, "weight": "bold"}},
            {"text": "kWh", "color": MUTED, "font": {"size": 13}},
        ]
    }
    options["cutoutPercentage"] = 45
    options["legend"] = {"display": False}
    options["layout"]["padding"] = {"top": 8, "right": 25, "bottom": 12, "left": 25}
    config = {
        "type": "doughnut",
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
    return _url(config, version="2", height=540)


def daily_podium_url(days: Sequence[DailyUsage]) -> str:
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
    options = _options()
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
    return _url(config, height=540)


def price_columns_url(current: PriceDistribution, previous: PriceDistribution | None = None) -> str:
    """Show each price band on a shared zero-based scale."""
    values = _values(current)
    prior = _values(previous) if previous is not None else None
    options = _options()
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
