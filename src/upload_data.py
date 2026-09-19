"""Insert PostgreSQL invoices/details, checking existing data in the same transaction."""

from collections import Counter
from dataclasses import dataclass

import psycopg
from loguru import logger
from psycopg import sql

from src.config import DatabaseSettings
from src.process_data import Invoice


class UploadConflict(ValueError):
    """Existing records cannot be matched safely; the upload is rolled back."""


@dataclass
class UploadResult:
    invoices_inserted: int = 0
    details_inserted: int = 0
    invoices_skipped: int = 0


def missing_details(invoice: Invoice, existing: list[tuple]) -> list[tuple]:
    """Compare exact rows as multisets so repeated DST hours retain their multiplicity."""
    source = Counter(detail.insert_values(1)[:-1] for detail in invoice.details)
    stored = Counter(tuple(row) for row in existing)
    if stored - source:
        raise UploadConflict("Stored details differ from the CSV; no data was overwritten")
    return list((source - stored).elements())


def upload_invoices(
    invoices: list[Invoice],
    settings: DatabaseSettings,
    username: str | None,
) -> UploadResult:
    """Commit the entire batch or roll back; never write login or client records."""
    if not username:
        raise ValueError("USER_ENERGIAXXI_1 must identify an existing login.username")
    result = UploadResult()
    if not invoices:
        return result
    tables = {
        name: sql.Identifier(settings.schema, name)
        for name in ("login", "client", "invoice", "detail")
    }
    with psycopg.connect(
        host=settings.host,
        dbname=settings.dbname,
        user=settings.user,
        password=settings.password,
        port=settings.port,
        sslmode=settings.sslmode,
        connect_timeout=10,
    ) as connection:
        # Each query after waiting for the locks must see the latest committed records.
        connection.isolation_level = psycopg.IsolationLevel.READ_COMMITTED
        with connection.cursor() as cursor:
            cursor.execute("SET LOCAL lock_timeout = '30s'")
            # Serialize check+insert, including databases without a unique invoice index.
            # Ordinary SELECTs remain possible; other invoice/detail writers must wait.
            cursor.execute(
                sql.SQL("LOCK TABLE {invoice}, {detail} IN SHARE ROW EXCLUSIVE MODE").format(
                    **tables
                )
            )
            for invoice in invoices:
                cursor.execute(
                    sql.SQL(
                        "SELECT c.id FROM {client} c JOIN {login} l ON l.id = c.id_login "
                        "WHERE c.cups = %s AND l.username = %s FOR SHARE OF c, l"
                    ).format(**tables),
                    (invoice.cups, username),
                )
                clients = cursor.fetchall()
                if not clients:
                    logger.warning(
                        "Client not found in the database for CUPS {} and the configured "
                        "username. Add the client record before importing. "
                        "Upload aborted; this batch will be rolled back.",
                        invoice.cups,
                    )
                if len(clients) != 1:
                    raise UploadConflict(
                        "Expected exactly one existing client for CUPS and username"
                    )
                client_id = clients[0][0]
                cursor.execute(
                    sql.SQL(
                        "SELECT id, tariff FROM {invoice} "
                        "WHERE id_client = %s AND initial_date = %s AND final_date = %s"
                    ).format(**tables),
                    (client_id, invoice.initial_date, invoice.final_date),
                )
                matches = cursor.fetchall()
                if len(matches) > 1:
                    raise UploadConflict(
                        "Multiple invoices already exist for this client and period"
                    )
                if matches:
                    invoice_id, tariff = matches[0]
                    if tariff != invoice.tariff:
                        raise UploadConflict("Existing invoice tariff differs from the CSV")
                    cursor.execute(
                        sql.SQL(
                            "SELECT date, hour, consumption, price, cost_per_hour "
                            "FROM {detail} WHERE id_invoice = %s"
                        ).format(**tables),
                        (invoice_id,),
                    )
                    missing = missing_details(invoice, cursor.fetchall())
                    if not missing:
                        result.invoices_skipped += 1
                        continue
                else:
                    cursor.execute(
                        sql.SQL(
                            "INSERT INTO {invoice} (initial_date, final_date, tariff, id_client) "
                            "VALUES (%s, %s, %s, %s) RETURNING id"
                        ).format(**tables),
                        invoice.insert_values(client_id),
                    )
                    invoice_id = cursor.fetchone()[0]
                    result.invoices_inserted += 1
                    missing = missing_details(invoice, [])
                cursor.executemany(
                    sql.SQL(
                        "INSERT INTO {detail} "
                        "(date, hour, consumption, price, cost_per_hour, id_invoice) "
                        "VALUES (%s, %s, %s, %s, %s, %s)"
                    ).format(**tables),
                    [(*row, invoice_id) for row in missing],
                )
                result.details_inserted += len(missing)
    return result
