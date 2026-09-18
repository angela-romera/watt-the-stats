import json
import os
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse

# noinspection PyUnresolvedReferences
from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SETTINGS_FILE = PROJECT_ROOT / "config" / "settings.json"


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
    )
