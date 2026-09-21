"""Download consumption CSVs, process them, and insert invoice data into PostgreSQL."""

import argparse

from loguru import logger

from src.config import load_database_settings, load_settings
from src.process_data import transform_csv, write_staging
from src.scraper import WebScraper


def main(argv: list[str] | None = None) -> None:
    """Run the full workflow unless processing-only mode is requested."""
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument(
        "--process-only",
        action="store_true",
        help="Transform existing CSVs without database writes",
    )
    mode.add_argument(
        "--upload",
        action="store_true",
        help="Download, transform, and insert into PostgreSQL (default)",
    )
    args = parser.parse_args(argv)
    settings = load_settings()
    if not args.process_only:
        if not settings.usernames:
            raise ValueError(
                "USER_ENERGIAXXI_LIST and PWD_ENERGIAXXI_LIST must contain credentials"
            )
        logger.info("Starting download from {}", settings.url)
        downloaded_files = []
        for index, (username, password) in enumerate(
            zip(settings.usernames, settings.passwords, strict=True), start=1
        ):
            logger.info("Downloading account {} of {}", index, len(settings.usernames))
            with WebScraper(settings, username, password) as scraper:
                downloaded_files.extend(scraper.download())
        logger.info("Download phase finished: {} file(s) saved", len(downloaded_files))

    paths = sorted(settings.download_dir.glob("*.csv"))
    if not paths:
        logger.warning("No CSV files found in {}", settings.download_dir)
        return
    if not args.process_only:
        from src.import_files import import_files

        result = import_files(paths, settings, load_database_settings())
        logger.info(
            "Files: {} imported, {} duplicates, {} failed, {} pending. "
            "Committed: {} new invoices, {} new details",
            result.imported,
            result.duplicates,
            result.failed,
            result.pending,
            result.invoices_inserted,
            result.details_inserted,
        )
        if result.failed or result.pending:
            raise SystemExit(1)
        return
    invoices = [transform_csv(path) for path in paths]
    for path, invoice in zip(paths, invoices, strict=True):
        write_staging(invoice, settings.processed_dir / f"{path.stem}.json")
    logger.info(
        "Processed {} invoice(s), {} detail rows",
        len(invoices),
        sum(len(invoice.details) for invoice in invoices),
    )


if __name__ == "__main__":
    main()
