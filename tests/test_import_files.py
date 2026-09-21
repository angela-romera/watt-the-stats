from dataclasses import dataclass
from datetime import date
from pathlib import Path

import psycopg
import pytest

import src.import_files as importer
from src.config import DatabaseSettings, Settings
from src.process_data import TransformationError
from src.upload_data import UploadConflict, UploadResult


@dataclass
class FakeInvoice:
    name: str
    cups: str = "ES0000000000000000AA"
    initial_date: date = date(2024, 1, 1)
    final_date: date = date(2024, 1, 31)


def make_settings(tmp_path: Path) -> Settings:
    return Settings(
        url="https://example.test",
        browser="chrome",
        headless=True,
        wait_timeout_seconds=1,
        download_timeout_seconds=1,
        periods_to_download=1,
        download_dir=tmp_path / "downloads",
        usernames=["existing-user"],
        passwords=["test-password"],
        processed_dir=tmp_path / "processed",
        imported_dir=tmp_path / "imported",
        duplicates_dir=tmp_path / "duplicates",
        failed_dir=tmp_path / "failed",
    )


DATABASE = DatabaseSettings("host", "database", "user", "password")


def files(tmp_path: Path, *names: str) -> list[Path]:
    paths = []
    for name in names:
        path = tmp_path / "downloads" / name
        path.parent.mkdir(exist_ok=True)
        path.write_text("csv", encoding="utf-8")
        paths.append(path)
    return paths


def patch_success(monkeypatch, outcomes):
    monkeypatch.setattr(
        importer,
        "transform_csv",
        lambda path: FakeInvoice(path.name),
    )
    monkeypatch.setattr(importer, "write_staging", lambda invoice, target: target.write_text("{}"))

    def upload(invoices, database, username):
        outcome = outcomes[invoices[0].name]
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome

    monkeypatch.setattr(importer, "upload_invoices", upload)


def test_import_duplicate_and_failed_files_are_classified_and_archived(tmp_path, monkeypatch):
    settings = make_settings(tmp_path)
    imported, duplicate, failed = files(tmp_path, "imported.csv", "duplicate.csv", "failed.csv")
    patch_success(
        monkeypatch,
        {
            "imported.csv": UploadResult(1, 2, 0),
            "duplicate.csv": UploadResult(0, 0, 1),
            "failed.csv": UploadConflict("client mismatch"),
        },
    )
    summary = importer.import_files([imported, duplicate, failed], settings, DATABASE)

    assert (summary.imported, summary.duplicates, summary.failed, summary.pending) == (1, 1, 1, 0)
    assert not imported.exists() and not duplicate.exists() and not failed.exists()
    assert (settings.imported_dir / imported.name).exists()
    assert (settings.duplicates_dir / duplicate.name).exists()
    assert (settings.failed_dir / failed.name).exists()
    assert not (settings.processed_dir / "imported.json").exists()
    assert not (settings.processed_dir / "duplicate.json").exists()
    assert (settings.processed_dir / "failed.json").exists()
    assert (settings.processed_dir / "import_report.json").exists()


def test_invalid_csv_and_missing_client_are_failed(tmp_path, monkeypatch):
    settings = make_settings(tmp_path)
    invalid, missing = files(tmp_path, "invalid.csv", "missing-client.csv")
    patch_success(
        monkeypatch,
        {"invalid.csv": UploadResult(), "missing-client.csv": UploadConflict("client not found")},
    )
    monkeypatch.setattr(
        importer,
        "transform_csv",
        lambda path: (
            (_ for _ in ()).throw(TransformationError("bad CSV"))
            if path.name == "invalid.csv"
            else FakeInvoice(path.name)
        ),
    )
    summary = importer.import_files([invalid, missing], settings, DATABASE)

    assert summary.failed == 2
    assert all(not path.exists() for path in (invalid, missing))


def test_operational_error_leaves_current_and_remaining_files_pending(tmp_path, monkeypatch):
    settings = make_settings(tmp_path)
    first, second, third = files(tmp_path, "first.csv", "second.csv", "third.csv")
    patch_success(monkeypatch, {"first.csv": psycopg.OperationalError("connection lost")})
    summary = importer.import_files([first, second, third], settings, DATABASE)

    assert summary.pending == 3
    assert all(path.exists() for path in (first, second, third))
    assert [item["status"] for item in summary.files] == ["pending", "pending", "pending"]
    assert (settings.processed_dir / "first.json").exists()


def test_archive_failure_keeps_source_pending(tmp_path, monkeypatch):
    settings = make_settings(tmp_path)
    source = files(tmp_path, "invoice.csv")[0]
    patch_success(monkeypatch, {source.name: UploadResult(1, 1, 0)})
    monkeypatch.setattr(
        importer,
        "archive_file",
        lambda source, folder: (_ for _ in ()).throw(OSError("archive unavailable")),
    )

    summary = importer.import_files([source], settings, DATABASE)

    assert summary.pending == 1
    assert summary.imported == 0
    assert source.exists()
    assert summary.files[0]["database_outcome"] == "imported"
    assert not (settings.processed_dir / "invoice.json").exists()


def test_staging_cleanup_failure_preserves_committed_outcome(tmp_path, monkeypatch):
    settings = make_settings(tmp_path)
    source = files(tmp_path, "invoice.csv")[0]
    patch_success(monkeypatch, {source.name: UploadResult(1, 1, 0)})
    staging = settings.processed_dir / "invoice.json"
    original_unlink = Path.unlink

    def unlink(path, *args, **kwargs):
        if path == staging:
            raise PermissionError("staging file is locked")
        return original_unlink(path, *args, **kwargs)

    monkeypatch.setattr(Path, "unlink", unlink)

    summary = importer.import_files([source], settings, DATABASE)

    assert (summary.imported, summary.failed, summary.pending) == (1, 0, 0)
    assert (summary.invoices_inserted, summary.details_inserted) == (1, 1)
    assert staging.exists()
    assert (settings.imported_dir / source.name).exists()
    assert summary.files[0]["cleanup_error"] == "staging file is locked"


def test_successful_files_continue_after_one_file_fails(tmp_path, monkeypatch):
    settings = make_settings(tmp_path)
    bad, good = files(tmp_path, "bad.csv", "good.csv")
    patch_success(
        monkeypatch,
        {"bad.csv": TransformationError("bad CSV"), "good.csv": UploadResult(1, 1, 0)},
    )
    monkeypatch.setattr(
        importer,
        "transform_csv",
        lambda path: (
            (_ for _ in ()).throw(TransformationError("bad CSV"))
            if path.name == "bad.csv"
            else FakeInvoice(path.name)
        ),
    )
    summary = importer.import_files([bad, good], settings, DATABASE)

    assert (summary.failed, summary.imported) == (1, 1)
    assert not bad.exists() and not good.exists()


def test_archive_name_collision_preserves_both_originals(tmp_path, monkeypatch):
    settings = make_settings(tmp_path)
    source = files(tmp_path, "invoice.csv")[0]
    settings.imported_dir.mkdir(parents=True)
    (settings.imported_dir / source.name).write_text("older", encoding="utf-8")
    patch_success(monkeypatch, {source.name: UploadResult(1, 1, 0)})

    importer.import_files([source], settings, DATABASE)

    assert (settings.imported_dir / "invoice.csv").read_text(encoding="utf-8") == "older"
    assert (settings.imported_dir / "invoice_1.csv").read_text(encoding="utf-8") == "csv"
    assert not source.exists()


@pytest.mark.parametrize("error", [OSError("copy failed")])
def test_archive_copy_failure_leaves_source_pending(tmp_path, monkeypatch, error):
    settings = make_settings(tmp_path)
    source = files(tmp_path, "invoice.csv")[0]
    patch_success(monkeypatch, {source.name: UploadResult(1, 1, 0)})
    monkeypatch.setattr(importer.shutil, "copyfileobj", lambda *args: (_ for _ in ()).throw(error))

    summary = importer.import_files([source], settings, DATABASE)

    assert summary.pending == 1
    assert source.exists()


def test_pending_count_accumulates_archive_failure_before_operational_error(tmp_path, monkeypatch):
    settings = make_settings(tmp_path)
    archived_failure, connection_failure = files(tmp_path, "archive.csv", "connection.csv")
    patch_success(
        monkeypatch,
        {connection_failure.name: psycopg.OperationalError("connection lost")},
    )
    calls = {"count": 0}

    def upload(invoices, database, username):
        calls["count"] += 1
        if calls["count"] == 1:
            return UploadResult(1, 1, 0)
        raise psycopg.OperationalError("connection lost")

    monkeypatch.setattr(importer, "upload_invoices", upload)
    monkeypatch.setattr(
        importer,
        "archive_file",
        lambda source, folder: (
            (_ for _ in ()).throw(OSError("archive unavailable"))
            if source.name == "archive.csv"
            else importer.archive_file(source, folder)
        ),
    )

    summary = importer.import_files([archived_failure, connection_failure], settings, DATABASE)

    assert summary.pending == 2
