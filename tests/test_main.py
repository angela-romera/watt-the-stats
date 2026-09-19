from unittest.mock import MagicMock, Mock

import pytest

import main as entrypoint
import src.import_files as importer
from src.import_files import ImportSummary
from tests.test_import_files import DATABASE, files, make_settings


@pytest.fixture
def workflow(tmp_path, monkeypatch):
    settings = make_settings(tmp_path)
    settings.download_dir.mkdir()
    monkeypatch.setattr(entrypoint, "load_settings", lambda: settings)
    database = Mock(return_value=DATABASE)
    upload = Mock(return_value=ImportSummary(imported=1, invoices_inserted=1))
    monkeypatch.setattr(entrypoint, "load_database_settings", database)
    monkeypatch.setattr(importer, "import_files", upload)
    scraper = MagicMock()
    scraper.return_value.__enter__.return_value.download.return_value = []
    monkeypatch.setattr(entrypoint, "WebScraper", scraper)
    return settings, database, upload


@pytest.mark.parametrize("args", [[], ["--upload"]])
def test_upload_modes_import_existing_and_downloaded_csvs(tmp_path, workflow, args):
    settings, database, upload = workflow
    paths = files(tmp_path, "invoice.csv")
    (settings.download_dir / "ignored.xls").write_text("ignored")
    download = entrypoint.WebScraper.return_value.__enter__.return_value.download
    download.side_effect = lambda: files(tmp_path, "new.csv")

    entrypoint.main(args)

    database.assert_called_once_with()
    entrypoint.WebScraper.assert_called_once_with(settings)
    download.assert_called_once_with()
    upload.assert_called_once_with(paths + [settings.download_dir / "new.csv"], settings, DATABASE)


def test_process_only_does_not_load_database_or_upload(tmp_path, monkeypatch, workflow):
    settings, database, upload = workflow
    paths = files(tmp_path, "invoice.csv")
    invoice = Mock(details=(object(),))
    transform = Mock(return_value=invoice)
    staging = Mock()
    monkeypatch.setattr(entrypoint, "transform_csv", transform)
    monkeypatch.setattr(entrypoint, "write_staging", staging)

    entrypoint.main(["--process-only"])

    database.assert_not_called()
    upload.assert_not_called()
    entrypoint.WebScraper.assert_not_called()
    transform.assert_called_once_with(paths[0])
    staging.assert_called_once_with(invoice, settings.processed_dir / "invoice.json")
    assert paths[0].exists()


def test_no_csvs_does_not_connect(workflow):
    _, database, upload = workflow

    entrypoint.main([])

    database.assert_not_called()
    upload.assert_not_called()


@pytest.mark.parametrize("summary", [ImportSummary(failed=1), ImportSummary(pending=1)])
def test_unsuccessful_import_exits_nonzero(tmp_path, workflow, summary):
    _, _, upload = workflow
    files(tmp_path, "invoice.csv")
    upload.return_value = summary

    with pytest.raises(SystemExit) as exc:
        entrypoint.main([])

    assert exc.value.code == 1


def test_conflicting_modes_exit_before_loading_settings(monkeypatch):
    settings = Mock()
    monkeypatch.setattr(entrypoint, "load_settings", settings)

    with pytest.raises(SystemExit) as exc:
        entrypoint.main(["--upload", "--process-only"])

    assert exc.value.code == 2
    settings.assert_not_called()
