from datetime import date
from decimal import Decimal
from io import BytesIO

import pytest
from PIL import Image

from src import report_charts
from src.report_calculations import DailyUsage, PriceDistribution


def distribution(*values):
    amounts = tuple(Decimal(str(value)) for value in values)
    total = sum(amounts)
    return PriceDistribution(
        *amounts, *(amount * 100 / total if total else Decimal(0) for amount in amounts)
    )


@pytest.mark.parametrize("values", [(0, 0, 0), (1, 0, 0), (0.004, 0.04, 1), (150, 90, 30)])
def test_charts_render_valid_pngs_for_zero_single_band_and_small_values(values):
    current = distribution(*values)
    for content in (
        report_charts.price_pie_png(current),
        report_charts.price_columns_png(current),
        report_charts.price_columns_png(current, distribution(0, 0, 0)),
    ):
        with Image.open(BytesIO(content)) as chart:
            assert chart.format == "PNG"
            assert chart.size == (1296, 828)
            chart.verify()


def test_pie_slices_use_actual_proportions(monkeypatch):
    monkeypatch.setattr(report_charts, "_png", lambda figure: figure)
    figure = report_charts.price_pie_png(distribution(3, 1, 0))
    wedges = figure.axes[0].patches
    assert [wedge.theta2 - wedge.theta1 for wedge in wedges] == pytest.approx([270, 90, 0])


def test_podium_places_winner_in_center_and_second_on_left(monkeypatch):
    monkeypatch.setattr(report_charts, "_png", lambda figure: figure)
    days = [
        DailyUsage(date(2026, 9, day), Decimal(amount))
        for day, amount in [(3, 10), (1, 30), (2, 20)]
    ]
    figure = report_charts.daily_podium_png(days, Decimal(15))
    labels = figure.axes[0].texts
    positions = {label.get_text(): label.get_position()[0] for label in labels}
    assert positions["1"] == 1
    assert positions["2"] == 0
    assert positions["3"] == 2
    assert positions["01/09/2026"] == positions["1"]
    assert positions["02/09/2026"] == positions["2"]
    assert positions["03/09/2026"] == positions["3"]


def test_podium_preserves_equal_ranks_and_does_not_invent_missing_days(monkeypatch):
    monkeypatch.setattr(report_charts, "_png", lambda figure: figure)
    days = [DailyUsage(date(2026, 9, day), Decimal(10)) for day in (1, 2)]
    figure = report_charts.daily_podium_png(days, Decimal(10))
    labels = [label.get_text() for label in figure.axes[0].texts]
    assert labels.count("1") == 2
    assert "2" not in labels
    assert "3" not in labels
    assert len(figure.axes[0].patches) == 2


def test_columns_keep_a_shared_zero_baseline_and_small_values_visible(monkeypatch):
    monkeypatch.setattr(report_charts, "_png", lambda figure: figure)
    figure = report_charts.price_columns_png(distribution(0.1, 0.2, 0.3), distribution(0.3, 0.1, 0))
    axis = figure.axes[0]
    assert [bar.get_height() for bar in axis.patches] == pytest.approx([0.1, 0.2, 0.3, 0.3, 0.1, 0])
    assert all(bar.get_y() == 0 for bar in axis.patches)
    assert axis.get_ylim() == pytest.approx((0, 0.375))
    assert report_charts.format_kwh(Decimal("0.004")) == "<0,01"
