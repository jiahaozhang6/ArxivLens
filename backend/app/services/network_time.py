from __future__ import annotations

import asyncio
import logging
import socket
import struct
import time
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from email.utils import parsedate_to_datetime
from statistics import median
from urllib.parse import urlparse
from zoneinfo import ZoneInfo

import httpx
from apscheduler.triggers.cron import CronTrigger

from app.config import get_settings

logger = logging.getLogger("arxiv-digest-time")
NTP_EPOCH_DELTA = 2_208_988_800


@dataclass(frozen=True)
class TimeSample:
    source: str
    method: str
    offset_seconds: float
    round_trip_seconds: float


@dataclass(frozen=True)
class NetworkTimeSnapshot:
    status: str
    source: str | None
    offset_seconds: float
    round_trip_ms: float | None
    synchronized_at: datetime | None
    last_attempt_at: datetime | None
    error: str | None

    @property
    def synchronized(self) -> bool:
        return self.status in {"synchronized", "stale"}


def _decode_ntp_timestamp(data: bytes, offset: int) -> float:
    seconds, fraction = struct.unpack_from("!II", data, offset)
    return seconds - NTP_EPOCH_DELTA + fraction / 2**32


def _encode_ntp_timestamp(packet: bytearray, offset: int, timestamp: float) -> None:
    ntp_timestamp = timestamp + NTP_EPOCH_DELTA
    seconds = int(ntp_timestamp)
    fraction = int((ntp_timestamp - seconds) * 2**32)
    struct.pack_into("!II", packet, offset, seconds, fraction)


def _query_ntp_server(host: str, timeout: float) -> TimeSample:
    packet = bytearray(48)
    packet[0] = 0x23
    started_at = time.time()
    _encode_ntp_timestamp(packet, 40, started_at)
    addresses = socket.getaddrinfo(host, 123, type=socket.SOCK_DGRAM)
    addresses.sort(key=lambda item: item[0] != socket.AF_INET)
    last_error: OSError | None = None
    for family, socket_type, protocol, _, address in addresses[:1]:
        connection = socket.socket(family, socket_type, protocol)
        connection.settimeout(timeout)
        try:
            connection.sendto(packet, address)
            response, _ = connection.recvfrom(512)
            received_at = time.time()
        except OSError as exc:
            last_error = exc
            continue
        finally:
            connection.close()
        if len(response) < 48:
            raise ValueError(f"Invalid NTP response from {host}")
        stratum = response[1]
        if stratum == 0 or stratum > 15:
            raise ValueError(f"Unusable NTP stratum from {host}: {stratum}")
        server_received_at = _decode_ntp_timestamp(response, 32)
        server_sent_at = _decode_ntp_timestamp(response, 40)
        offset_seconds = (
            (server_received_at - started_at) + (server_sent_at - received_at)
        ) / 2
        round_trip_seconds = max(
            0.0,
            (received_at - started_at) - (server_sent_at - server_received_at),
        )
        return TimeSample(
            source=host,
            method="NTP",
            offset_seconds=offset_seconds,
            round_trip_seconds=round_trip_seconds,
        )
    if last_error:
        raise last_error
    raise OSError(f"No address available for NTP server {host}")


async def _query_http_date(client: httpx.AsyncClient, url: str) -> TimeSample:
    started_at = time.time()
    response = await client.head(url, headers={"Cache-Control": "no-cache"})
    received_at = time.time()
    response.raise_for_status()
    date_header = response.headers.get("date")
    if not date_header:
        raise ValueError(f"HTTPS time source returned no Date header: {url}")
    server_time = parsedate_to_datetime(date_header)
    if server_time.tzinfo is None:
        server_time = server_time.replace(tzinfo=UTC)
    midpoint = (started_at + received_at) / 2
    return TimeSample(
        source=urlparse(url).netloc,
        method="HTTPS",
        offset_seconds=server_time.timestamp() - midpoint,
        round_trip_seconds=max(0.0, received_at - started_at),
    )


def _choose_sample(samples: list[TimeSample]) -> tuple[float, TimeSample]:
    selected_offset = float(median(sample.offset_seconds for sample in samples))
    reference = min(
        samples,
        key=lambda sample: (
            abs(sample.offset_seconds - selected_offset),
            sample.round_trip_seconds,
        ),
    )
    return selected_offset, reference


class NetworkClock:
    def __init__(self) -> None:
        self._offset_seconds = 0.0
        self._source: str | None = None
        self._round_trip_ms: float | None = None
        self._status = "system_fallback"
        self._synchronized_at: datetime | None = None
        self._last_attempt_at: datetime | None = None
        self._last_attempt_monotonic = 0.0
        self._error: str | None = None
        self._lock = asyncio.Lock()

    def utcnow(self) -> datetime:
        return datetime.now(UTC) + timedelta(seconds=self._offset_seconds)

    def snapshot(self) -> NetworkTimeSnapshot:
        return NetworkTimeSnapshot(
            status=self._status,
            source=self._source,
            offset_seconds=self._offset_seconds,
            round_trip_ms=self._round_trip_ms,
            synchronized_at=self._synchronized_at,
            last_attempt_at=self._last_attempt_at,
            error=self._error,
        )

    async def sync(self, force: bool = False) -> NetworkTimeSnapshot:
        settings = get_settings()
        if not settings.network_time_enabled:
            self._status = "disabled"
            self._offset_seconds = 0.0
            self._source = None
            return self.snapshot()

        now_monotonic = time.monotonic()
        retry_interval = (
            settings.network_time_sync_interval_seconds
            if self._synchronized_at
            else min(300, settings.network_time_sync_interval_seconds)
        )
        if (
            not force
            and self._last_attempt_monotonic
            and now_monotonic - self._last_attempt_monotonic < retry_interval
        ):
            return self.snapshot()

        async with self._lock:
            now_monotonic = time.monotonic()
            if (
                not force
                and self._last_attempt_monotonic
                and now_monotonic - self._last_attempt_monotonic < retry_interval
            ):
                return self.snapshot()
            self._last_attempt_monotonic = now_monotonic
            self._last_attempt_at = datetime.now(UTC)
            errors: list[str] = []
            ntp_results = await asyncio.gather(
                *(
                    asyncio.to_thread(
                        _query_ntp_server,
                        host,
                        settings.network_time_timeout_seconds,
                    )
                    for host in settings.network_time_server_list
                ),
                return_exceptions=True,
            )
            samples = [result for result in ntp_results if isinstance(result, TimeSample)]
            errors.extend(str(result) for result in ntp_results if isinstance(result, Exception))

            if not samples and settings.network_time_http_url_list:
                async with httpx.AsyncClient(
                    timeout=settings.network_time_timeout_seconds,
                    follow_redirects=True,
                ) as client:
                    http_results = await asyncio.gather(
                        *(
                            _query_http_date(client, url)
                            for url in settings.network_time_http_url_list
                        ),
                        return_exceptions=True,
                    )
                samples = [
                    result for result in http_results if isinstance(result, TimeSample)
                ]
                errors.extend(
                    str(result) for result in http_results if isinstance(result, Exception)
                )

            if samples:
                offset_seconds, reference = _choose_sample(samples)
                self._offset_seconds = offset_seconds
                self._source = f"{reference.method} {reference.source}"
                self._round_trip_ms = reference.round_trip_seconds * 1000
                self._status = "synchronized"
                self._synchronized_at = self.utcnow()
                self._error = None
                logger.info(
                    "Network time synchronized via %s; offset %.3fs, round trip %.1fms",
                    self._source,
                    self._offset_seconds,
                    self._round_trip_ms,
                )
            else:
                self._status = "stale" if self._synchronized_at else "system_fallback"
                self._error = "; ".join(errors[:3]) or "No network time source responded"
                if not self._synchronized_at:
                    self._offset_seconds = 0.0
                    self._source = None
                    self._round_trip_ms = None
                logger.warning("Network time synchronization failed: %s", self._error)
            return self.snapshot()


def next_daily_run_utc(
    now_utc: datetime,
    timezone_name: str,
    hour: int,
    minute: int,
) -> datetime:
    trigger = CronTrigger(hour=hour, minute=minute, timezone=ZoneInfo(timezone_name))
    next_fire = trigger.get_next_fire_time(None, now_utc)
    if next_fire is None:
        raise ValueError("Unable to calculate the next scheduled run")
    return next_fire.astimezone(UTC)


network_clock = NetworkClock()
