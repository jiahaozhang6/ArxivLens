from datetime import UTC, datetime
from zoneinfo import ZoneInfo

from app.models import RunLog, RunStatus
from app.worker import _scheduled_retry_trigger, _scheduled_run_is_due


def test_schedule_waits_until_configured_local_time():
    timezone = ZoneInfo("Asia/Shanghai")
    now = datetime(2026, 9, 12, 22, 30, tzinfo=UTC)

    assert not _scheduled_run_is_due(now, timezone, 7, 0, None, 0)


def test_schedule_catches_up_any_time_after_configured_time():
    timezone = ZoneInfo("Asia/Shanghai")
    now = datetime(2026, 9, 13, 7, 30, tzinfo=UTC)
    yesterday = datetime(2026, 9, 11, 23, 0, tzinfo=UTC)

    assert _scheduled_run_is_due(now, timezone, 7, 0, None, 0)
    assert _scheduled_run_is_due(now, timezone, 7, 0, yesterday, 0)


def test_schedule_runs_only_once_per_local_day():
    timezone = ZoneInfo("Asia/Shanghai")
    now = datetime(2026, 9, 13, 7, 30, tzinfo=UTC)
    today = datetime(2026, 9, 12, 23, 0, tzinfo=UTC)

    assert not _scheduled_run_is_due(now, timezone, 7, 0, today, 0)


def test_degraded_scheduled_run_retries_after_30_minutes():
    timezone = ZoneInfo("Asia/Shanghai")
    started = datetime(2026, 9, 14, 5, 0, tzinfo=UTC)
    run = RunLog(
        trigger="scheduled",
        status=RunStatus.partial,
        started_at=started,
        finished_at=started,
        error_details={"retry_recommended": True},
    )

    assert _scheduled_retry_trigger(
        [run],
        datetime(2026, 9, 14, 5, 29, tzinfo=UTC),
        timezone,
        0,
    ) is None
    assert _scheduled_retry_trigger(
        [run],
        datetime(2026, 9, 14, 5, 30, tzinfo=UTC),
        timezone,
        0,
    ) == "scheduled_retry_1"
