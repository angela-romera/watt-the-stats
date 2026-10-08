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
PODIUM_KWH_FORMATTER = (
    "function(value,context){return context.dataset.actualKwh[context.dataIndex];}"
)
PODIUM_KWH_FORMATTER_PLACEHOLDER = "__PODIUM_KWH_FORMATTER__"
WHOLE_KWH_FORMATTER = "function(value){return Math.round(value).toString();}"
WHOLE_KWH_FORMATTER_PLACEHOLDER = "__WHOLE_KWH_FORMATTER__"
COMPARISON_KWH_FORMATTER = "function(value){return Math.round(value)+' kWh';}"
COMPARISON_KWH_FORMATTER_PLACEHOLDER = "__COMPARISON_KWH_FORMATTER__"
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
    chart = chart.replace(json.dumps(PODIUM_KWH_FORMATTER_PLACEHOLDER), PODIUM_KWH_FORMATTER)
    chart = chart.replace(json.dumps(WHOLE_KWH_FORMATTER_PLACEHOLDER), WHOLE_KWH_FORMATTER)
    chart = chart.replace(
        json.dumps(COMPARISON_KWH_FORMATTER_PLACEHOLDER), COMPARISON_KWH_FORMATTER
    )
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
        "font": {"family": "Arial", "size": 30, "weight": "bold"},
        "formatter": PIE_PERCENT_FORMATTER_PLACEHOLDER,
    }
    options["plugins"]["doughnutlabel"] = {
        "labels": [
            {"text": str(rounded_total), "color": INK, "font": {"size": 70, "weight": "bold"}},
            {"text": "kWh", "color": MUTED, "font": {"size": 30}},
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
    """Render the top days as a stepped podium, with the winner in the center."""
    if any(not item.kwh.is_finite() or item.kwh < 0 for item in days):
        raise ValueError("Chart consumption must be finite and non-negative")
    ranked = sorted(days, key=lambda item: (-item.kwh, item.day))[:3]
    ordered = [ranked[index] if index < len(ranked) else None for index in (1, 0, 2)]
    labels = []
    heights = []
    colors = []
    consumption_labels = []
    podium_colors = {1: "#364152", 2: "#778397", 3: "#B9C1CC"}
    for item in ordered:
        if item is None:
            labels.append("")
            heights.append(0)
            colors.append("#E5EBF2")
            consumption_labels.append("")
            continue
        place = 1 + sum(other.kwh > item.kwh for other in ranked)
        labels.append(f"{item.day:%d/%m/%Y}")
        heights.append(4 - place)
        colors.append(podium_colors.get(place, "#A9BACD"))
        rounded_kwh = int(item.kwh.quantize(Decimal("1"), rounding=ROUND_HALF_UP))
        consumption_labels.append(f"{rounded_kwh} kWh")
    options = _options()
    options["plugins"]["datalabels"] = {
        "display": bool(ranked),
        "anchor": "end",
        "align": "top",
        "offset": 4,
        "color": INK,
        "font": {"family": "Arial", "size": 30, "weight": "bold"},
        "formatter": PODIUM_KWH_FORMATTER_PLACEHOLDER,
    }
    options["layout"]["padding"] = {"top": 20, "right": 25, "bottom": 12, "left": 25}
    options["scales"] = {
        "x": {
            "grid": {"display": False},
            "border": {"display": False},
            "ticks": {"color": INK, "font": {"family": "Arial", "size": 30}},
        },
        "y": {
            "beginAtZero": True,
            "max": 3.4,
            "grid": {"display": False},
            "border": {"display": False},
            "ticks": {"display": False},
        },
    }
    config = {
        "type": "bar",
        "data": {
            "labels": labels if ranked else ["", "Sense dades diaries", ""],
            "datasets": [
                {
                    "data": heights,
                    "actualKwh": consumption_labels,
                    "backgroundColor": colors,
                    "borderRadius": 8,
                    "barPercentage": 0.98,
                    "categoryPercentage": 0.98,
                }
            ],
        },
        "options": options,
    }
    return _url(config, height=540)

def price_columns_url(current: PriceDistribution, previous: PriceDistribution | None = None) -> str:
    """Compare price bands, using the top-days visual style when history exists."""
    values = _values(current)
    prior = _values(previous) if previous is not None else None
    options = _options()
    options["plugins"]["datalabels"] = {
        "display": True,
        "anchor": "end",
        "align": "end",
        "offset": 3,
        "color": INK,
        "font": {"family": "Arial", "size": 15 if prior is not None else 11, "weight": "bold"},
        "formatter": (
            COMPARISON_KWH_FORMATTER_PLACEHOLDER
            if prior is not None
            else WHOLE_KWH_FORMATTER_PLACEHOLDER
        ),
    }
    options["scales"] = {
        "x": {
            "grid": {"display": False},
            "border": {"color": GRID},
            "ticks": {
                "color": INK,
                "font": {
                    "family": "Arial",
                    "size": 15 if prior is not None else 13,
                    "weight": "bold" if prior is not None else "normal",
                },
            },
        },
        "y": {
            "beginAtZero": True,
            "ticks": {
                "precision": 0,
                "display": prior is None,
                "color": MUTED,
                "maxTicksLimit": 5,
            },
            "grid": {"display": prior is None, "color": GRID},
            "border": {"display": False},
            "title": {"display": prior is None, "text": "kWh", "color": MUTED},
        },
    }
    largest_value = max(values + (prior or [0]))
    if prior is not None:
        # Keep every report's paired bars on a zero-based scale sized to its own data.
        options["scales"]["y"]["max"] = largest_value * 1.18 if largest_value else 1
    else:
        options["scales"]["y"]["suggestedMax"] = largest_value * 1.2 if largest_value else 1
    chart_labels = list(BAND_LABELS)
    datasets = [
        {
            "label": "Per??ode actual",
            "data": values,
            "backgroundColor": list(BAND_COLORS),
            "borderRadius": 13,
            "barPercentage": 0.82,
            "categoryPercentage": 0.72,
        }
    ]
    if prior is not None:
        chart_labels = ["Actual", "Any anterior"] * len(BAND_LABELS)
        comparison_values = [value for pair in zip(values, prior, strict=True) for value in pair]
        comparison_colors = [
            color
            for current_color, previous_color in zip(
                BAND_COLORS, PREVIOUS_BAND_COLORS, strict=True
            )
            for color in (current_color, previous_color)
        ]
        datasets = [
            {
                "label": "Consum per franja",
                "data": comparison_values,
                "backgroundColor": comparison_colors,
                "borderRadius": 13,
                "barPercentage": 0.96,
                "categoryPercentage": 0.94,
            }
        ]
    return _url(
        {"type": "bar", "data": {"labels": chart_labels, "datasets": datasets}, "options": options},
        height=320,
    )
