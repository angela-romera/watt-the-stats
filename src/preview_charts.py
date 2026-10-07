"""Render a local chart gallery with synthetic data; no database, SMTP or uploads."""

from datetime import date
from decimal import Decimal
from pathlib import Path

from loguru import logger

from src.config import PROJECT_ROOT
from src.report_calculations import DailyUsage, PriceDistribution
from src.report_charts import daily_podium_png, price_columns_png, price_pie_png


def _distribution(*amounts: str) -> PriceDistribution:
    values = tuple(Decimal(amount) for amount in amounts)
    total = sum(values)
    percentages = tuple(value * 100 / total if total else Decimal(0) for value in values)
    return PriceDistribution(*values, *percentages)


def create_preview(output_dir: Path) -> Path:
    """Save all three chart types and their edge cases alongside a local HTML gallery."""
    output_dir.mkdir(parents=True, exist_ok=True)
    current = _distribution("156.7", "92.4", "34.2")
    previous = _distribution("121.4", "113.7", "49.6")
    empty = _distribution("0", "0", "0")
    days = (
        DailyUsage(date(2026, 9, 12), Decimal("18.6")),
        DailyUsage(date(2026, 9, 24), Decimal("16.2")),
        DailyUsage(date(2026, 9, 5), Decimal("14.9")),
    )
    charts = {
        "pie": price_pie_png(current),
        "podium": daily_podium_png(days, Decimal("9.44")),
        "columns": price_columns_png(current, previous),
        "pie-empty": price_pie_png(empty),
        "pie-small-slices": price_pie_png(_distribution("500", "0.04", "0.02")),
        "podium-empty": daily_podium_png((), Decimal(0)),
        "podium-one-day": daily_podium_png(days[:1], Decimal("9.44")),
        "podium-ties": daily_podium_png(
            tuple(DailyUsage(item.day, Decimal("16.2")) for item in days), Decimal("9.44")
        ),
        "columns-current-only": price_columns_png(current),
        "columns-empty": price_columns_png(empty, empty),
        "columns-small-values": price_columns_png(
            _distribution("0.005", "0.035", "0.1"), _distribution("0", "0.05", "0.02")
        ),
    }
    for name, data in charts.items():
        (output_dir / f"{name}.png").write_bytes(data)
    images = "".join(
        f'<figure><img src="{name}.png" alt="{name}" width="648">'
        f"<figcaption>{name}</figcaption></figure>"
        for name in charts
    )
    path = output_dir / "index.html"
    path.write_text(
        '<!doctype html><html lang="ca"><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1">'
        "<title>Watt the Stats · Chart preview</title>"
        "<style>body{background:#f4f7fb;color:#223247;font:16px Arial,sans-serif;"
        "max-width:680px;margin:32px auto;padding:0 12px}"
        "figure{margin:24px 0}img{display:block;max-width:100%;height:auto;margin:auto}"
        "figcaption{text-align:center;color:#68788c;padding:6px}</style>"
        "<h1>Watt the Stats</h1><p>Matplotlib · Dades fictícies de demostració</p>"
        f"{images}</html>",
        encoding="utf-8",
    )
    return path


if __name__ == "__main__":
    logger.info("Chart preview: {}", create_preview(PROJECT_ROOT / "data/processed/chart-preview"))
