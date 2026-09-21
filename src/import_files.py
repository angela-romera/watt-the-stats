"""Import and archive individual CSVs according to their committed database outcome."""

import json
import os
import shutil
from dataclasses import asdict, dataclass, field
from pathlib import Path

import psycopg
from loguru import logger

from src.config import DatabaseSettings, Settings
from src.process_data import TransformationError, transform_csv, write_staging
from src.upload_data import UploadConflict, upload_invoices


@dataclass
class ImportSummary:
    imported: int = 0
    duplicates: int = 0
    failed: int = 0
    pending: int = 0
    invoices_inserted: int = 0
    details_inserted: int = 0
    files: list[dict] = field(default_factory=list)


def archive_file(source: Path, folder: Path) -> Path:
    """Preserve existing archives, and remove the source only after a complete copy."""
    source = source.resolve(strict=True)
    folder = folder.resolve()
    if source.parent == folder:
        raise ValueError("Archive folder must differ from the source folder")
    folder.mkdir(parents=True, exist_ok=True)
    suffix = 0
    while True:
        name = source.name if not suffix else f"{source.stem}_{suffix}{source.suffix}"
        target = folder / name
        try:
            output = target.open("xb")
            break
        except FileExistsError:
            suffix += 1
    try:
        with output, source.open("rb") as original:
            shutil.copyfileobj(original, output)
            output.flush()
            os.fsync(output.fileno())
        shutil.copystat(source, target)
        source.unlink()
    except OSError:
        target.unlink(missing_ok=True)
        raise
    return target


def _validate_folders(settings: Settings) -> None:
    folders = [
        path.resolve()
        for path in (
            settings.download_dir,
            settings.processed_dir,
            settings.imported_dir,
            settings.duplicates_dir,
            settings.failed_dir,
        )
    ]
    for index, folder in enumerate(folders):
        for other in folders[index + 1 :]:
            if folder == other or folder in other.parents or other in folder.parents:
                raise ValueError("Download, processed, and archive folders must be separate")
    for folder in folders[1:]:
        folder.mkdir(parents=True, exist_ok=True)


def import_files(
    paths: list[Path],
    settings: Settings,
    database: DatabaseSettings,
) -> ImportSummary:
    """Use one transaction per file; connection/commit uncertainty leaves files pending."""
    _validate_folders(settings)
    if any(path.resolve().parent != settings.download_dir.resolve() for path in paths):
        raise ValueError("Import files must be directly inside the configured download folder")
    if not settings.usernames:
        raise ValueError("USER_ENERGIAXXI_LIST must list existing login.username values")
    summary = ImportSummary()
    destinations = {
        "imported": settings.imported_dir,
        "duplicates": settings.duplicates_dir,
        "failed": settings.failed_dir,
    }
    for index, path in enumerate(paths):
        record = {"file": path.name}
        staging = settings.processed_dir / f"{path.stem}.json"
        try:
            invoice = transform_csv(path)
            record.update(
                cups=invoice.cups,
                initial_date=invoice.initial_date.isoformat(),
                final_date=invoice.final_date.isoformat(),
            )
            write_staging(invoice, staging)
            result = upload_invoices([invoice], database, settings.usernames)
        except (
            TransformationError,
            UploadConflict,
            psycopg.DataError,
            psycopg.IntegrityError,
        ) as exc:
            # These failures are either before any writes or follow a confirmed rollback.
            status = "failed"
            record["reason"] = str(exc)
            logger.error("{} failed: {}", path.name, exc)
        except (psycopg.Error, OSError) as exc:
            # A lost connection during COMMIT cannot safely be classified as a failure.
            # Retrying later checks the database before inserting anything else.
            summary.pending += len(paths) - index
            record.update(status="pending", reason=str(exc))
            summary.files.append(record)
            summary.files.extend(
                {
                    "file": item.name,
                    "status": "pending",
                    "reason": "Not attempted after an operational error",
                }
                for item in paths[index + 1 :]
            )
            logger.error("Import stopped; {} file(s) remain in downloads: {}", summary.pending, exc)
            break
        else:
            status = "duplicates" if result.invoices_skipped else "imported"
            summary.invoices_inserted += result.invoices_inserted
            summary.details_inserted += result.details_inserted
            record["invoices_inserted"] = result.invoices_inserted
            record["details_inserted"] = result.details_inserted
            # The uploader has committed; subsequent runs use the CSV and database.
            try:
                staging.unlink(missing_ok=True)
            except OSError as exc:
                record["cleanup_error"] = str(exc)
                logger.warning(
                    "Database upload confirmed, but could not remove {}: {}", staging, exc
                )
        try:
            target = archive_file(path, destinations[status])
        except OSError as exc:
            summary.pending += 1
            record.update(status="pending", database_outcome=status, reason=str(exc))
            logger.error(
                "{} outcome is {}, but archiving failed; source retained: {}",
                path.name,
                status,
                exc,
            )
        else:
            setattr(summary, status, getattr(summary, status) + 1)
            record.update(status=status, archive=str(target))
            logger.info("{} -> {}", path.name, status)
        summary.files.append(record)
    report = settings.processed_dir / "import_report.json"
    temporary = report.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(asdict(summary), indent=2) + "\n", encoding="utf-8")
    temporary.replace(report)
    return summary
