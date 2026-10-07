"""Matplotlib charts for invoice reports; rendering never sends or uploads data."""

from collections.abc import Sequence
from decimal import ROUND_HALF_UP, Decimal
from io import BytesIO
from math import isfinite

import matplotlib as mpl
from matplotlib.axes import Axes
from matplotlib.backends.backend_agg import FigureCanvasAgg
from matplotlib.colors import LinearSegmentedColormap, to_rgb
from matplotlib.figure import Figure
from matplotlib.patches import FancyBboxPatch
from matplotlib.ticker import FuncFormatter, MaxNLocator

from src.report_calculations import (
    CHEAP_PRICE_THRESHOLD_EUR_PER_KWH,
    EXPENSIVE_PRICE_THRESHOLD_EUR_PER_KWH,
    DailyUsage,
    PriceDistribution,
)

INK = "#223247"
MUTED = "#68788C"
GRID = "#E5EBF2"
BAND_COLORS = ("#198875", "#E5A637", "#CE646B")
PREVIOUS_BAND_COLORS = ("#BADBD6", "#F7E4C3", "#F0D0D3")
BAND_LABELS = ("Preu baix", "Preu mitjà", "Preu alt")
STYLE = {"font.family": "DejaVu Sans", "font.size": 10, "text.color": INK}


def format_kwh(value: Decimal | float) -> str:
    """Round displayed consumption to whole kWh."""
    amount = Decimal(str(value))
    return str(int(amount.quantize(Decimal("1"), rounding=ROUND_HALF_UP)))


def _figure() -> Figure:
    figure = Figure(figsize=(7.2, 4.6), dpi=180, facecolor="none")
    FigureCanvasAgg(figure)
    return figure


def _png(figure: Figure) -> bytes:
    output = BytesIO()
    # Fixed canvas dimensions keep every chart aligned at the same email width.
    figure.savefig(
        output,
        format="png",
        dpi=180,
        transparent=True,
        facecolor="none",
        edgecolor="none",
    )
    figure.clear()
    return output.getvalue()


def _values(distribution: PriceDistribution) -> list[float]:
    values = [
        float(distribution.cheap_kwh),
        float(distribution.medium_kwh),
        float(distribution.expensive_kwh),
    ]
    if any(not isfinite(value) or value < 0 for value in values):
        raise ValueError("Chart consumption must be finite and non-negative")
    return values


def _tint(color: str, amount: float = 0.7) -> tuple[float, ...]:
    return tuple(channel + (1 - channel) * amount for channel in to_rgb(color))


def price_pie_png(distribution: PriceDistribution) -> bytes:
    """A doughnut with proportional slices, percentages, and total consumption."""
    values = _values(distribution)
    total = sum(values)
    rounded_total = int(
        (distribution.cheap_kwh + distribution.medium_kwh + distribution.expensive_kwh).quantize(
            Decimal("1"), rounding=ROUND_HALF_UP
        )
    )
    with mpl.rc_context(STYLE):
        figure = _figure()
        axis = figure.add_axes((0.28, 0.30, 0.44, 0.57))
        axis.set_aspect("equal")
        if total:
            _wedges, _labels, percentages = axis.pie(
                values,
                colors=BAND_COLORS,
                startangle=90,
                counterclock=False,
                autopct=lambda value: f"{value:.0f}%" if value >= 6 else "",
                pctdistance=0.80,
                wedgeprops={"edgecolor": "white", "linewidth": 3, "width": 0.38},
                textprops={"fontsize": 12, "weight": "bold", "color": "white"},
            )
        else:
            axis.pie([1], colors=[GRID], wedgeprops={"edgecolor": "white", "width": 0.38})
        axis.text(0, 0.16, str(rounded_total), ha="center", va="center", fontsize=18, weight="bold")
        axis.text(0, -0.04, "kWh", ha="center", va="center", fontsize=11, color=MUTED)
        axis.set_axis_off()
        return _png(figure)


def daily_podium_png(days: Sequence[DailyUsage], daily_average: Decimal) -> bytes:
    """Rank the highest-consumption days, with the winner centered and ties explicit."""
    if any(not item.kwh.is_finite() or item.kwh < 0 for item in days):
        raise ValueError("Chart consumption must be finite and non-negative")
    ranked = sorted(days, key=lambda item: (-item.kwh, item.day))[:3]
    with mpl.rc_context(STYLE):
        figure = _figure()
        axis = figure.add_axes((0.085, 0.195, 0.83, 0.66))
        axis.set(xlim=(-0.6, 2.6), ylim=(0, 2.15))
        axis.set_axis_off()
        axis.axhline(0.08, color=GRID, linewidth=1.5)
        colors = ("#E8B34D", "#A9BACD", "#C68D6D")
        if not ranked:
            axis.text(1, 1, "Sense dades diàries", color=MUTED, ha="center", va="center")
        for index, item in enumerate(ranked):
            x = (1, 0, 2)[index]
            rank = 1 + sum(other.kwh > item.kwh for other in ranked)
            height = (1.2, 0.9, 0.65)[rank - 1]
            color = colors[rank - 1]
            block = FancyBboxPatch(
                (x - 0.41, 0.08),
                0.82,
                height,
                boxstyle="round,pad=0,rounding_size=0.045",
                facecolor=color,
                edgecolor="none",
                zorder=2,
            )
            axis.add_patch(block)
            gradient = LinearSegmentedColormap.from_list("podium", [color, _tint(color, 0.35)])
            axis.imshow(
                [[step / 159] for step in range(160)],
                extent=(x - 0.41, x + 0.41, 0.08, 0.08 + height),
                origin="lower",
                cmap=gradient,
                aspect="auto",
                zorder=3,
                clip_path=block,
                clip_on=True,
            )
            axis.text(
                x,
                height + 0.38,
                f"{format_kwh(item.kwh)} kWh",
                ha="center",
                fontsize=14,
                weight="bold",
            )
            axis.text(
                x, height + 0.21, f"{item.day:%d/%m/%Y}", ha="center", fontsize=9, color=MUTED
            )
            axis.text(
                x,
                0.08 + height * 0.53,
                str(rank),
                ha="center",
                va="center",
                fontsize=29,
                weight="bold",
                color=INK,
                bbox={"boxstyle": "circle,pad=0.20", "fc": "white", "ec": "none", "alpha": 0.65},
                zorder=4,
            )
        figure.text(
            0.5,
            0.117,
            f"Mitjana del període  ·  {format_kwh(daily_average)} kWh/dia",
            ha="center",
            fontsize=10,
            weight="bold",
        )
        figure.text(
            0.5,
            0.068,
            "L'alçada del podi indica la posició, no els kWh.",
            ha="center",
            fontsize=8,
            color=MUTED,
        )
        return _png(figure)


def _column_axes(figure: Figure, maximum: float) -> Axes:
    axis = figure.add_axes((0.115, 0.22, 0.805, 0.66))
    axis.set_ylim(0, maximum * 1.25 if maximum else 1)
    axis.set_xlim(-0.65, 2.65)
    axis.spines[["top", "right", "left"]].set_visible(False)
    axis.spines["bottom"].set_color(GRID)
    axis.yaxis.set_major_locator(MaxNLocator(nbins=4, integer=True))
    axis.yaxis.set_major_formatter(FuncFormatter(lambda value, _position: f"{value:.0f}"))
    axis.tick_params(axis="both", length=0, labelsize=10, colors=MUTED, pad=8)
    axis.set_xticks((0, 1, 2), BAND_LABELS)
    axis.grid(axis="y", color=GRID, linewidth=0.8)
    axis.set_axisbelow(True)
    return axis


def price_columns_png(
    current: PriceDistribution,
    previous: PriceDistribution | None = None,
) -> bytes:
    """Grouped columns on one zero-based scale; use current-only columns without history."""
    values = _values(current)
    prior = _values(previous) if previous is not None else None
    with mpl.rc_context(STYLE):
        figure = _figure()
        axis = _column_axes(figure, max(values + (prior or [0])))
        axis.text(0, 1.04, "kWh", transform=axis.transAxes, fontsize=9, color=MUTED)
        positions = (0, 1, 2)
        width = 0.28 if prior is not None else 0.42
        current_bars = axis.bar(
            [position - (0.17 if prior is not None else 0) for position in positions],
            values,
            width=width,
            color=BAND_COLORS,
            zorder=3,
        )
        axis.bar_label(
            current_bars,
            labels=[format_kwh(value) for value in values],
            padding=6,
            fontsize=10,
            weight="bold",
            color=INK,
        )
        if prior is not None:
            prior_bars = axis.bar(
                [position + 0.17 for position in positions],
                prior,
                width=width,
                color=PREVIOUS_BAND_COLORS,
                zorder=3,
            )
            axis.bar_label(
                prior_bars,
                labels=[format_kwh(value) for value in prior],
                padding=6,
                fontsize=10,
                color=MUTED,
            )
        low = str(CHEAP_PRICE_THRESHOLD_EUR_PER_KWH).replace(".", ",")
        high = str(EXPENSIVE_PRICE_THRESHOLD_EUR_PER_KWH).replace(".", ",")
        figure.text(
            0.5,
            0.095,
            f"Baix ≤ {low}  ·  Mitjà > {low} i < {high}  ·  Alt ≥ {high} EUR/kWh",
            ha="center",
            fontsize=8,
            color=MUTED,
        )
        return _png(figure)
