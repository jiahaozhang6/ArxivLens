from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

from app.services import network_time
from app.services.network_time import NetworkClock, TimeSample, next_daily_run_utc


def test_next_daily_run_uses_selected_timezone():
    now = datetime(2026, 9, 12, 0, 15, tzinfo=UTC)

    next_run = next_daily_run_utc(now, "Asia/Shanghai", 8, 0)

    assert next_run == datetime(2026, 9, 13, 0, 0, tzinfo=UTC)


@pytest.mark.asyncio
async def test_network_clock_uses_median_ntp_offset(monkeypatch):
    settings = SimpleNamespace(
        network_time_enabled=True,
        network_time_sync_interval_seconds=1800,
        network_time_timeout_seconds=1.0,
        network_time_server_list=["one", "two", "three"],
        network_time_http_url_list=[],
    )
    samples = {
        "one": TimeSample("one", "NTP", 0.10, 0.04),
        "two": TimeSample("two", "NTP", 0.25, 0.02),
        "three": TimeSample("three", "NTP", 9.0, 0.01),
    }
    monkeypatch.setattr(network_time, "get_settings", lambda: settings)
    monkeypatch.setattr(
        network_time,
        "_query_ntp_server",
        lambda host, _timeout: samples[host],
    )
    clock = NetworkClock()

    snapshot = await clock.sync()

    assert snapshot.status == "synchronized"
    assert snapshot.source == "NTP two"
    assert snapshot.offset_seconds == pytest.approx(0.25)
    assert snapshot.round_trip_ms == pytest.approx(20.0)


@pytest.mark.asyncio
async def test_network_clock_falls_back_to_system_time(monkeypatch):
    settings = SimpleNamespace(
        network_time_enabled=True,
        network_time_sync_interval_seconds=1800,
        network_time_timeout_seconds=1.0,
        network_time_server_list=["unavailable"],
        network_time_http_url_list=[],
    )
    monkeypatch.setattr(network_time, "get_settings", lambda: settings)

    def fail_query(_host, _timeout):
        raise OSError("network unavailable")

    monkeypatch.setattr(network_time, "_query_ntp_server", fail_query)
    clock = NetworkClock()

    snapshot = await clock.sync(force=True)

    assert snapshot.status == "system_fallback"
    assert snapshot.synchronized is False
    assert snapshot.offset_seconds == 0
    assert "network unavailable" in (snapshot.error or "")
