from datetime import datetime
from unittest.mock import Mock
from zoneinfo import ZoneInfo

import pytest

from src import scheduler


@pytest.mark.parametrize(
    ("current", "expected"),
    [
        ("2026-01-01T00:00:00", "2026-01-10T14:00:00"),
        ("2026-01-10T13:59:59", "2026-01-10T14:00:00"),
        ("2026-01-10T14:00:00", "2026-01-25T14:00:00"),
        ("2026-01-25T14:00:01", "2026-02-10T14:00:00"),
        ("2028-02-29T00:00:00", "2028-03-10T14:00:00"),
        ("2026-12-26T00:00:00", "2027-01-10T14:00:00"),
        ("2026-03-26T00:00:00", "2026-04-10T14:00:00"),
        ("2026-10-25T14:00:01", "2026-11-10T14:00:00"),
    ],
)
def test_next_run_respects_monthly_schedule_and_local_time(current, expected):
    zone = ZoneInfo("Europe/Madrid")
    now = datetime.fromisoformat(current).replace(tzinfo=zone)
    result = scheduler.next_run("0 14 10,25 * *", "Europe/Madrid", now)
    assert result == datetime.fromisoformat(expected).replace(tzinfo=zone)


@pytest.mark.parametrize("schedule", ["invalid", "* * * * * *", "60 14 10,25 * *"])
def test_invalid_schedule_is_rejected(schedule):
    with pytest.raises(ValueError, match="five-field"):
        scheduler.next_run(schedule, "UTC")


@pytest.mark.parametrize("error", [RuntimeError("Import failed"), SystemExit(1)])
def test_failed_import_does_not_stop_the_scheduler(monkeypatch, error):
    monkeypatch.setattr(scheduler, "load_dotenv", Mock())
    monkeypatch.setenv("SCHEDULE_CRON", "0 14 10,25 * *")
    monkeypatch.setenv("TIMEZONE", "UTC")
    run = Mock(side_effect=error)
    pause = Mock(side_effect=[None, KeyboardInterrupt])
    monkeypatch.setattr(scheduler, "run_import", run)
    monkeypatch.setattr(scheduler, "sleep", pause)

    with pytest.raises(KeyboardInterrupt):
        scheduler.main()

    run.assert_called_once_with([])
    assert pause.call_count == 2
