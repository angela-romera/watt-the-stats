import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlparse

# noinspection PyUnresolvedReferences
from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SETTINGS_FILE = PROJECT_ROOT / "config" / "settings.json"


@dataclass(frozen=True)
class DatabaseSettings:
    host: str
    dbname: str
    user: str
    password: str = field(repr=False)
    port: int = 5432
    schema: str = "public"
    sslmode: str = "prefer"


def load_database_settings() -> DatabaseSettings:
    """Require PostgreSQL credentials only when an upload is requested."""
    load_dotenv(PROJECT_ROOT / ".env")
    required = ("SERVER_WTS", "DB_WTS_ELEC", "USER_DB_WTS", "PWD_DB_WTS")
    missing = [name for name in required if not os.getenv(name)]
    if missing:
        raise ValueError(f"Missing PostgreSQL settings: {', '.join(missing)}")
    port = int(os.getenv("PORT_DB_WTS") or "5432")
    if not 1 <= port <= 65535:
        raise ValueError("PORT_DB_WTS must be between 1 and 65535")
    return DatabaseSettings(
        host=os.environ["SERVER_WTS"],
        dbname=os.environ["DB_WTS_ELEC"],
        user=os.environ["USER_DB_WTS"],
        password=os.environ["PWD_DB_WTS"],
        port=port,
        schema=os.getenv("SCHEMA_DB_WTS") or "public",
        sslmode=os.getenv("SSLMODE_DB_WTS") or "prefer",
    )


@dataclass(frozen=True)
class Settings:
    url: str
    browser: str
    headless: bool
    wait_timeout_seconds: int
    download_timeout_seconds: int
    periods_to_download: int
    download_dir: Path
    username: str | None
    password: str | None
    processed_dir: Path = PROJECT_ROOT / "data" / "processed"
    imported_dir: Path = PROJECT_ROOT / "data" / "imported"
    duplicates_dir: Path = PROJECT_ROOT / "data" / "duplicates"
    failed_dir: Path = PROJECT_ROOT / "data" / "failed"


def load_settings() -> Settings:
    """Load non-secret settings from JSON and credentials from `.env`."""
    load_dotenv(PROJECT_ROOT / ".env")

    with SETTINGS_FILE.open(encoding="utf-8") as settings_file:
        values = json.load(settings_file)

    url = values["url"].strip()
    parsed_url = urlparse(url)
    if parsed_url.scheme not in {"http", "https"} or not parsed_url.netloc:
        raise ValueError("config/settings.json must contain a valid HTTP(S) URL")

    browser = values.get("browser", "chrome").strip().lower()
    if browser not in {"chrome", "edge"}:
        raise ValueError("browser must be either 'chrome' or 'edge'")

    download_dir = PROJECT_ROOT / values.get("download_dir", "data/downloads")
    download_dir.mkdir(parents=True, exist_ok=True)

    return Settings(
        url=url,
        browser=browser,
        headless=bool(values.get("headless", False)),
        wait_timeout_seconds=int(values.get("wait_timeout_seconds", 30)),
        download_timeout_seconds=int(values.get("download_timeout_seconds", 60)),
        periods_to_download=int(values.get("periods_to_download", 6)),
        download_dir=download_dir.resolve(),
        username=os.getenv("USER_ENERGIAXXI_1"),
        password=os.getenv("PWD_ENERGIAXXI_1"),
        processed_dir=(PROJECT_ROOT / values.get("processed_dir", "data/processed")).resolve(),
        imported_dir=(PROJECT_ROOT / values.get("imported_dir", "data/imported")).resolve(),
        duplicates_dir=(PROJECT_ROOT / values.get("duplicates_dir", "data/duplicates")).resolve(),
        failed_dir=(PROJECT_ROOT / values.get("failed_dir", "data/failed")).resolve(),
    )
