from datetime import UTC, datetime
from types import SimpleNamespace

from app.models import RunLog, RunStatus
from app.routers.system import _schedule_state


def _schedule():
    return SimpleNamespace(
        enabled=True,
        timezone="Asia/Shanghai",
        hour=13,
        minute=0,
    )


def test_schedule_state_is_pending_before_daily_time():
    state, scheduled_at = _schedule_state(
        _schedule(),
        None,
        datetime(2026, 9, 15, 4, 30, tzinfo=UTC),
        0,
    )

    assert state == "pending"
    assert scheduled_at == datetime(2026, 9, 15, 5, 0, tzinfo=UTC)


def test_schedule_state_marks_missing_run_overdue_after_grace_period():
    state, _ = _schedule_state(
        _schedule(),
        None,
        datetime(2026, 9, 15, 5, 6, tzinfo=UTC),
        0,
    )

    assert state == "overdue"


def test_schedule_state_reports_todays_run_result():
    run = RunLog(
        trigger="scheduled",
        status=RunStatus.completed,
        started_at=datetime(2026, 9, 15, 5, 0, tzinfo=UTC),
    )

    state, _ = _schedule_state(
        _schedule(),
        run,
        datetime(2026, 9, 15, 6, 0, tzinfo=UTC),
        0,
    )

    assert state == "completed"


def test_schedule_state_accepts_database_string_status():
    run = RunLog(
        trigger="scheduled",
        status="completed",
        started_at=datetime(2026, 9, 15, 5, 0, tzinfo=UTC),
    )

    state, _ = _schedule_state(
        _schedule(),
        run,
        datetime(2026, 9, 15, 6, 0, tzinfo=UTC),
        0,
    )

    assert state == "completed"
