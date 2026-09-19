import json
from datetime import date, time
from decimal import Decimal

import pytest

from src.process_data import (
    TransformationError,
    process_files,
    transform_csv,
    write_staging,
)

METADATA = "Fecha inicio:,01/01/2024\nFecha fin:,02/01/2024\nCUPS:,ES123\nTarifa:,2.0TD\n"


def csv_file(tmp_path, details, header="Precio (\u20ac/kWh)", total=None):
    rows = [METADATA, f"Fecha,Hora,Consumo (Wh),{header},Coste por hora (\u20ac)\n"]
    rows.extend(
        f"{day},{hour},{consumption},{price},{cost}\n"
        for day, hour, consumption, price, cost in details
    )
    if total is not None:
        rows.append(f"Total (Wh),{total}\n")
    path = tmp_path / "export.csv"
    path.write_text("".join(rows), encoding="utf-8")
    return path


def test_numeric_hours_and_interval_ranges(tmp_path):
    path = csv_file(
        tmp_path,
        [
            ("2024-01-01", "1", "10", "0.123456789012345", "1.23456789012345678901"),
            ("2024-01-02", "24", "20", "0.2", "2.0"),
            ("2024-01-01", "05:00-06:00", "30", "0.3", "3.0"),
        ],
        total="60",
    )

    invoice = transform_csv(path)

    assert invoice.details[0].date == date(2024, 1, 1)
    assert invoice.details[0].hour == time(0)
    assert invoice.details[1].date == date(2024, 1, 1)
    assert invoice.details[1].hour == time(23)
    assert invoice.details[2].hour == time(5)


def test_eur_per_wh_is_converted_to_eur_per_kwh_and_decimals_are_exact(tmp_path):
    path = csv_file(
        tmp_path,
        [("2024-01-01", "1", "1.0", "0.000123456789012345", "0.00000000012345678901")],
        header="Precio (\u20ac/Wh)",
        total="1.0",
    )

    detail = transform_csv(path).details[0]

    assert detail.price == Decimal("0.123456789012345")
    assert detail.cost_per_hour == Decimal("0.00000000012345678901")
    assert detail.consumption == Decimal("1.0")


@pytest.mark.parametrize(
    "field,value",
    [
        ("consumption", "1000000000.0"),
        ("price", "100.000000000000000"),
        ("cost", "100.000000000000000000000"),
    ],
)
def test_schema_overflow_is_rejected(tmp_path, field, value):
    values = {"consumption": "1.0", "price": "0.2", "cost": "0.2"}
    values[field] = value
    path = csv_file(
        tmp_path, [("2024-01-01", "1", values["consumption"], values["price"], values["cost"])]
    )

    with pytest.raises(TransformationError, match="does not fit decimal"):
        transform_csv(path)


@pytest.mark.parametrize(
    "field,value",
    [
        ("consumption", "1.01"),
        ("price", "0.1234567890123456"),
        ("cost", "0.123456789012345678901"),
    ],
)
def test_schema_precision_loss_is_rejected(tmp_path, field, value):
    values = {"consumption": "1.0", "price": "0.2", "cost": "0.2"}
    values[field] = value
    path = csv_file(
        tmp_path, [("2024-01-01", "1", values["consumption"], values["price"], values["cost"])]
    )

    with pytest.raises(TransformationError, match="loses precision"):
        transform_csv(path)


def test_total_mismatch_and_missing_metadata_are_rejected(tmp_path):
    with pytest.raises(TransformationError, match="Consumption does not match"):
        transform_csv(csv_file(tmp_path, [("2024-01-01", "1", "1.0", "0.2", "0.2")], total="2.0"))

    path = tmp_path / "missing.csv"
    path.write_text(
        "Fecha fin:,01/01/2024\nCUPS:,ES123\nTarifa:,2.0TD\n"
        "Fecha,Hora,Consumo (Wh),Precio (\u20ac/kWh),Coste por hora (\u20ac)\n"
        "2024-01-01,1,1.0,0.2,0.2\n",
        encoding="utf-8",
    )
    with pytest.raises(TransformationError):
        transform_csv(path)


def test_staging_json_and_identity_arguments(tmp_path):
    invoice = transform_csv(csv_file(tmp_path, [("2024-01-01", "1", "1.0", "0.2", "0.2")]))
    output = tmp_path / "processed" / "invoice.json"
    write_staging(invoice, output)
    payload = json.loads(output.read_text(encoding="utf-8"))

    assert payload["units"] == {"consumption": "Wh", "price": "EUR/kWh", "cost_per_hour": "EUR"}
    assert payload["details"][0]["consumption"] == "1.0"
    assert invoice.insert_values(7) == (date(2024, 1, 1), date(2024, 1, 2), "2.0TD", 7)
    assert invoice.details[0].insert_values(8)[-1] == 8
    with pytest.raises(TransformationError):
        invoice.insert_values(0)
    with pytest.raises(TransformationError):
        invoice.details[0].insert_values(True)


@pytest.mark.parametrize("hour", ["0", "25", "05:00-07:00", "nan"])
def test_invalid_hours_are_rejected(tmp_path, hour):
    path = csv_file(tmp_path, [("2024-01-01", hour, "1.0", "0.2", "0.2")])
    with pytest.raises(TransformationError):
        transform_csv(path)


def test_detail_outside_invoice_period_is_rejected(tmp_path):
    path = csv_file(tmp_path, [("2024-01-03", "1", "1.0", "0.2", "0.2")])
    with pytest.raises(TransformationError, match="outside the invoice period"):
        transform_csv(path)


def test_batch_validation_precedes_output(tmp_path):
    valid = csv_file(tmp_path, [("2024-01-01", "1", "1", "0.2", "0.0002")])
    invalid = tmp_path / "invalid.csv"
    invalid.write_text("invalid", encoding="utf-8")
    output_dir = tmp_path / "processed"
    with pytest.raises(TransformationError, match="Invalid metadata"):
        process_files([valid, invalid], output_dir)
    assert not output_dir.exists()
