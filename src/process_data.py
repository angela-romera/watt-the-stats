"""Transform portal exports into invoice/detail staging data without database writes."""

import csv
import json
import re
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from decimal import Decimal, InvalidOperation, localcontext
from pathlib import Path

from loguru import logger


class TransformationError(ValueError):
    """An export cannot be represented safely in the target schema."""


def sql_decimal(value: str | Decimal, precision: int, scale: int) -> Decimal:
    """Reject overflow and precision loss instead of silently rounding source data."""
    try:
        number = Decimal(value)
        if not number.is_finite() or abs(number) >= Decimal(10) ** (precision - scale):
            raise TransformationError(f"Value does not fit decimal({precision}, {scale})")
        with localcontext() as context:
            context.prec = max(precision + 2, len(number.as_tuple().digits) + scale + 2)
            result = number.quantize(Decimal(1).scaleb(-scale))
        if result != number:
            raise TransformationError(f"Value loses precision in decimal({precision}, {scale})")
        return result
    except InvalidOperation as exc:
        raise TransformationError("Invalid decimal value") from exc


def interval_start(day: str, hour: str) -> datetime:
    """Numeric hours are end timestamps; explicit ranges describe start timestamps."""
    parsed_day = date.fromisoformat(day)
    if "-" in hour:
        match = re.fullmatch(r"(\d{2}):00-(\d{2}):00", hour)
        if not match:
            raise TransformationError("Expected an hourly HH:00-HH:00 interval")
        start, end = map(int, match.groups())
        if not 0 <= start <= 23 or end != (start + 1) % 24:
            raise TransformationError("Invalid hourly interval")
        return datetime.combine(parsed_day, time(start))
    number = Decimal(hour)
    if not number.is_finite() or number != number.to_integral_value() or not 1 <= number <= 24:
        raise TransformationError("Numeric hours must be integers from 1 to 24")
    # The portal already advances the CSV date at hour 24 (midnight).
    return datetime.combine(parsed_day, time(int(number) % 24)) - timedelta(hours=1)


def _identity(value: int) -> int:
    if type(value) is not int or not 1 <= value <= 2147483647:
        raise TransformationError("An existing positive SQL int identity is required")
    return value


@dataclass(frozen=True)
class Detail:
    date: date
    hour: time
    consumption: Decimal
    price: Decimal
    cost_per_hour: Decimal

    def insert_values(self, id_invoice: int) -> tuple:
        """Values for date, hour, consumption, price, cost_per_hour, id_invoice."""
        return (
            self.date,
            self.hour,
            self.consumption,
            self.price,
            self.cost_per_hour,
            _identity(id_invoice),
        )


@dataclass(frozen=True)
class Invoice:
    cups: str
    initial_date: date
    final_date: date
    tariff: str
    details: tuple[Detail, ...]

    def insert_values(self, id_client: int) -> tuple:
        """Resolve cups against existing dbo.client before calling this method."""
        return self.initial_date, self.final_date, self.tariff, _identity(id_client)


def transform_csv(path: Path) -> Invoice:
    """Read an Energia XXI CSV, preserving Wh consumption and cost from the export."""
    line = 1
    try:
        with path.open(encoding="utf-8-sig", newline="") as source:
            rows = list(csv.reader(source, skipinitialspace=True))
        metadata = {}
        details = []
        header = None
        total = None
        for row_number, raw in enumerate(rows, 1):
            line = row_number
            row = [cell.strip() for cell in raw]
            if not row or not any(row):
                continue
            if header is None:
                if row[0] == "Fecha":
                    header = row
                    if (
                        len(row) != 5
                        or row[1] != "Hora"
                        or row[2] != "Consumo (Wh)"
                        or row[3] not in {"Precio (€/kWh)", "Precio (€/Wh)"}
                        or row[4] != "Coste por hora (€)"
                    ):
                        raise TransformationError("Unsupported CSV columns or units")
                elif len(row) == 2:
                    key = row[0].rstrip(": ")
                    if key in metadata:
                        raise TransformationError(f"Duplicate metadata: {key}")
                    metadata[key] = row[1]
                else:
                    raise TransformationError("Invalid metadata row")
                continue
            if row[0].startswith("Total (Wh)"):
                if total is not None or len(row) != 2:
                    raise TransformationError("Invalid or duplicate total")
                total = sql_decimal(row[1], 20, 1)
                continue
            if total is not None or len(row) != 5:
                raise TransformationError("Invalid detail row or data after total")
            stamp = interval_start(row[0], row[1])
            consumption = sql_decimal(row[2], 10, 1)
            price = Decimal(row[3])
            if header[3] == "Precio (€/Wh)":
                with localcontext() as context:
                    context.prec = max(30, len(price.as_tuple().digits) + 3)
                    price *= 1000
            details.append(
                Detail(
                    stamp.date(),
                    stamp.time(),
                    consumption,
                    sql_decimal(price, 17, 15),
                    sql_decimal(row[4], 22, 20),
                )
            )
        if header is None or not details:
            raise TransformationError("No consumption details found")
        initial = datetime.strptime(metadata["Fecha inicio"], "%d/%m/%Y").date()
        final = datetime.strptime(metadata["Fecha fin"], "%d/%m/%Y").date()
        cups, tariff = metadata["CUPS"], metadata["Tarifa"]
        if not cups or len(cups) > 50 or not tariff or len(tariff) > 10 or initial > final:
            raise TransformationError("Invalid invoice metadata")
        if any(not initial <= item.date <= final for item in details):
            raise TransformationError("Detail date falls outside the invoice period")
        if total is not None and sum(item.consumption for item in details) != total:
            raise TransformationError("Consumption does not match the CSV total")
        # Preserve repeated local hours: the schema has no timezone/DST column.
        return Invoice(cups, initial, final, tariff, tuple(details))
    except (ValueError, KeyError, InvalidOperation) as exc:
        raise TransformationError(f"{path.name}, line {line}: {exc}") from exc


def write_staging(invoice: Invoice, target: Path) -> None:
    """Write exact decimals as strings; identities are supplied by the future uploader."""
    payload = {
        "schema_version": 1,
        "client_lookup": {"cups": invoice.cups},
        "units": {"consumption": "Wh", "price": "EUR/kWh", "cost_per_hour": "EUR"},
        "hour_convention": "interval_start",
        "invoice": {
            "initial_date": invoice.initial_date.isoformat(),
            "final_date": invoice.final_date.isoformat(),
            "tariff": invoice.tariff,
        },
        "details": [
            {
                "date": item.date.isoformat(),
                "hour": item.hour.isoformat(),
                "consumption": str(item.consumption),
                "price": str(item.price),
                "cost_per_hour": str(item.cost_per_hour),
            }
            for item in invoice.details
        ],
    }
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    temporary.replace(target)


def process_files(paths: list[Path], processed_dir: Path) -> list[Path]:
    """Validate all inputs before writing one staging document per CSV."""
    invoices = [(path, transform_csv(path)) for path in paths]
    outputs = []
    for path, invoice in invoices:
        target = processed_dir / f"{path.stem}.json"
        write_staging(invoice, target)
        outputs.append(target)
        logger.info("Transformed {}: {} detail rows", path.name, len(invoice.details))
    return outputs
