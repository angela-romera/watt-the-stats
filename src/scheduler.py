"""Run the import workflow at the times defined by SCHEDULE_CRON."""

import os
from datetime import datetime
from time import sleep
from zoneinfo import ZoneInfo

from croniter import croniter
from dotenv import load_dotenv
from loguru import logger

from main import main as run_import
from src.config import PROJECT_ROOT


def next_run(schedule_cron: str, timezone: str, now: datetime | None = None) -> datetime:
    """Return the next cron occurrence in the configured time zone."""
    if len(schedule_cron.split()) != 5 or not croniter.is_valid(schedule_cron):
        raise ValueError("SCHEDULE_CRON must be a valid five-field cron expression")
    zone = ZoneInfo(timezone)
    current_time = now or datetime.now(zone)
    if current_time.tzinfo is None:
        raise ValueError("The scheduler requires a timezone-aware datetime")
    return croniter(schedule_cron, current_time.astimezone(zone)).get_next(datetime)


def main() -> None:
    """Keep the container alive and execute each scheduled import sequentially."""
    load_dotenv(PROJECT_ROOT / ".env")
    schedule = os.environ["SCHEDULE_CRON"]
    timezone = os.getenv("TIMEZONE") or "UTC"
    while True:
        scheduled = next_run(schedule, timezone)
        logger.info("Next import scheduled for {}", scheduled.isoformat())
        delay = max(scheduled.timestamp() - datetime.now(scheduled.tzinfo).timestamp(), 0)
        sleep(delay)
        try:
            run_import([])
        except (Exception, SystemExit):
            logger.exception("Scheduled import failed")


if __name__ == "__main__":
    main()
