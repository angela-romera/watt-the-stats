from loguru import logger

from src.config import load_settings
from src.scraper import WebScraper


def main() -> None:
    """Run the download phase of the project."""
    settings = load_settings()
    logger.info("Starting download from {}", settings.url)

    with WebScraper(settings) as scraper:
        downloaded_files = scraper.download()

    logger.info("Download phase finished: {} file(s) saved", len(downloaded_files))


if __name__ == "__main__":
    main()
