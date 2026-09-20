import asyncio
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from functools import partial
from types import SimpleNamespace

import httpx
import pytest
from filelock import FileLock

from app.services import arxiv as arxiv_service
from app.services.arxiv import (
    ArxivFetchBatch,
    fetch_papers_detailed,
    parse_atom_feed,
    parse_daily_atom_feed,
    parse_rss_feed,
)

SAMPLE_FEED = """<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom" xmlns:arxiv="http://arxiv.org/schemas/atom">
  <entry>
    <id>http://arxiv.org/abs/2609.01234v2</id>
    <updated>2026-09-12T01:00:00Z</updated>
    <published>2026-09-11T20:00:00Z</published>
    <title>  A   Reliable Paper Parser  </title>
    <summary>Line one.\nLine two.</summary>
    <author><name>Alice Researcher</name></author>
    <author><name>Bob Scientist</name></author>
    <category term="cs.AI" />
    <category term="cs.CL" />
    <arxiv:primary_category term="cs.AI" />
    <arxiv:comment>12 pages</arxiv:comment>
    <link href="https://arxiv.org/abs/2609.01234v2" rel="alternate" />
    <link title="pdf" href="https://arxiv.org/pdf/2609.01234v2" rel="related" />
  </entry>
</feed>
"""

SAMPLE_RSS_FEED = """<?xml version="1.0" encoding="UTF-8"?>
<rss xmlns:dc="http://purl.org/dc/elements/1.1/" version="2.0">
  <channel>
    <title>cs.AI updates on arXiv.org</title>
    <item>
      <title>A Practical World Model</title>
      <link>https://arxiv.org/abs/2609.09999v2</link>
      <description>&lt;p&gt;Abstract: A world model for reliable planning.&lt;/p&gt;</description>
      <dc:creator>Alice Researcher and Bob Scientist</dc:creator>
      <pubDate>Fri, 11 Sep 2026 20:00:00 +0000</pubDate>
      <category>cs.LG</category>
    </item>
  </channel>
</rss>
"""

SAMPLE_DAILY_ATOM_FEED = """<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom"
      xmlns:arxiv="http://arxiv.org/schemas/atom"
      xmlns:dc="http://purl.org/dc/elements/1.1/">
  <updated>2026-09-14T04:00:00Z</updated>
  <entry>
    <id>oai:arXiv.org:2609.12036v1</id>
    <title>Pelican-Sim: A General World Model Simulator</title>
    <updated>2026-09-14T04:00:00Z</updated>
    <published>2026-09-14T00:00:00-04:00</published>
    <link href="https://arxiv.org/abs/2609.12036" rel="alternate" />
    <summary>arXiv:2609.12036v1 Announce Type: new Abstract: A world model for embodied intelligence.</summary>
    <category term="cs.RO" />
    <category term="cs.AI" />
    <arxiv:announce_type>new</arxiv:announce_type>
    <dc:creator>Alice Researcher, Bob Scientist</dc:creator>
  </entry>
  <entry>
    <id>oai:arXiv.org:2609.12037v1</id>
    <title>Cross-listed World Model</title>
    <updated>2026-09-14T04:00:00Z</updated>
    <published>2026-09-14T00:00:00-04:00</published>
    <link href="https://arxiv.org/abs/2609.12037" rel="alternate" />
    <summary>arXiv:2609.12037v1 Announce Type: cross Abstract: Another world model.</summary>
    <category term="cs.LG" />
    <category term="cs.RO" />
    <arxiv:announce_type>cross</arxiv:announce_type>
    <dc:creator>Carol Author</dc:creator>
  </entry>
</feed>
"""


def test_parse_atom_feed_normalizes_metadata():
    papers = parse_atom_feed(SAMPLE_FEED)
    assert len(papers) == 1
    paper = papers[0]
    assert paper.arxiv_id == "2609.01234"
    assert paper.version == 2
    assert paper.title == "A Reliable Paper Parser"
    assert paper.abstract == "Line one. Line two."
    assert paper.authors == ["Alice Researcher", "Bob Scientist"]
    assert paper.primary_category == "cs.AI"
    assert paper.pdf_url.endswith("2609.01234v2")


def test_parse_rss_feed_normalizes_metadata():
    papers = parse_rss_feed(SAMPLE_RSS_FEED, "cs.AI")

    assert len(papers) == 1
    paper = papers[0]
    assert paper.arxiv_id == "2609.09999"
    assert paper.version == 2
    assert paper.abstract == "A world model for reliable planning."
    assert paper.authors == ["Alice Researcher", "Bob Scientist"]
    assert paper.categories == ["cs.LG", "cs.AI"]


def test_parse_daily_atom_feed_includes_announce_type_and_metadata():
    papers = parse_daily_atom_feed(SAMPLE_DAILY_ATOM_FEED, "cs.RO")

    assert [paper.arxiv_id for paper in papers] == ["2609.12036", "2609.12037"]
    assert papers[0].announce_type == "new"
    assert papers[0].abstract == "A world model for embodied intelligence."
    assert papers[0].authors == ["Alice Researcher", "Bob Scientist"]
    assert papers[1].announce_type == "cross"


@pytest.mark.asyncio
async def test_global_rate_lock_can_be_released_from_another_thread(monkeypatch, tmp_path):
    lock_path = tmp_path / "arxiv-rate.lock"
    monkeypatch.setattr(arxiv_service, "_rate_lock_path", lock_path)
    lock = arxiv_service._new_rate_lock()
    loop = asyncio.get_running_loop()

    with (
        ThreadPoolExecutor(max_workers=1) as acquire_pool,
        ThreadPoolExecutor(max_workers=1) as release_pool,
    ):
        await loop.run_in_executor(acquire_pool, partial(lock.acquire, timeout=1))
        await loop.run_in_executor(release_pool, lock.release)

        contender = FileLock(str(lock_path))
        contender.acquire(timeout=1)
        contender.release()


def _settings(**overrides):
    values = {
        "arxiv_cache_ttl_seconds": 21_600,
        "arxiv_contact_email": "tests@example.com",
        "arxiv_retry_attempts": 3,
        "arxiv_retry_base_seconds": 0,
        "arxiv_retry_max_seconds": 0,
        "arxiv_stale_cache_hours": 24,
        "arxiv_rss_cache_ttl_seconds": 10_800,
        "arxiv_rss_stale_cache_hours": 168,
        "arxiv_atom_cooldown_base_hours": 6,
        "arxiv_atom_cooldown_max_hours": 24,
        "openalex_enabled": False,
        "openalex_max_queries_per_topic": 3,
        "openalex_cache_ttl_seconds": 86_400,
        "openalex_stale_cache_hours": 168,
        "http_timeout_seconds": 1,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


@pytest.mark.asyncio
async def test_fetch_uses_rss_primary_without_calling_atom(monkeypatch):
    paper = parse_daily_atom_feed(SAMPLE_DAILY_ATOM_FEED, "cs.RO")[0]

    async def fake_rss(*_args, **_kwargs):
        return ArxivFetchBatch(papers=[paper], source="rss")

    async def unexpected(*_args, **_kwargs):
        raise AssertionError("healthy RSS must not call another discovery source")

    monkeypatch.setattr(arxiv_service, "_fetch_daily_rss", fake_rss)
    monkeypatch.setattr(arxiv_service, "_fetch_openalex", unexpected)
    monkeypatch.setattr(arxiv_service, "_request_with_global_limit", unexpected)

    batch = await fetch_papers_detailed(
        '(cat:cs.RO) AND all:"world model"',
        max_results=5,
        lookback_days=4,
    )

    assert batch.source == "rss"
    assert [item.arxiv_id for item in batch.papers] == ["2609.12036"]


@pytest.mark.asyncio
async def test_daily_rss_filters_cross_list(monkeypatch, tmp_path):
    class FixedDatetime(datetime):
        @classmethod
        def now(cls, tz=None):
            value = cls(2026, 9, 16, tzinfo=UTC)
            return value.astimezone(tz) if tz else value.replace(tzinfo=None)

    monkeypatch.setattr(arxiv_service, "_rss_cache_dir", tmp_path)
    monkeypatch.setattr(arxiv_service, "get_settings", lambda: _settings())
    monkeypatch.setattr(arxiv_service, "datetime", FixedDatetime)

    class FakeClient:
        async def get(self, *_args, **_kwargs):
            return httpx.Response(200, text=SAMPLE_DAILY_ATOM_FEED)

    without_cross = await arxiv_service._fetch_daily_rss(
        FakeClient(),
        '(cat:cs.RO) AND all:"world model"',
        10,
        4,
        {},
        False,
    )
    tmp_path.joinpath("cs.RO.json").unlink()
    with_cross = await arxiv_service._fetch_daily_rss(
        FakeClient(),
        '(cat:cs.RO) AND all:"world model"',
        10,
        4,
        {},
        True,
    )

    assert without_cross is not None
    assert with_cross is not None
    assert [paper.arxiv_id for paper in without_cross.papers] == ["2609.12036"]
    assert {paper.arxiv_id for paper in with_cross.papers} == {"2609.12036", "2609.12037"}


@pytest.mark.asyncio
async def test_atom_429_creates_persistent_cooldown(monkeypatch, tmp_path):
    monkeypatch.setattr(arxiv_service, "_cache_dir", tmp_path)
    monkeypatch.setattr(arxiv_service, "_cooldown_path", tmp_path / "cooldown.json")
    monkeypatch.setattr(arxiv_service, "get_settings", lambda: _settings())
    requests = 0

    async def no_rss(*_args, **_kwargs):
        return None

    async def no_openalex(*_args, **_kwargs):
        return None

    async def rate_limited(*_args, **_kwargs):
        nonlocal requests
        requests += 1
        return httpx.Response(429, text="Rate exceeded", headers={"Retry-After": "0"})

    monkeypatch.setattr(arxiv_service, "_fetch_daily_rss", no_rss)
    monkeypatch.setattr(arxiv_service, "_fetch_openalex", no_openalex)
    monkeypatch.setattr(arxiv_service, "_request_with_global_limit", rate_limited)

    with pytest.raises(RuntimeError, match=r"HTTP 429 Too Many Requests: Rate exceeded"):
        await fetch_papers_detailed("all:world models", max_results=1, lookback_days=10_000)
    with pytest.raises(RuntimeError, match="正处于限流冷却"):
        await fetch_papers_detailed("all:world models", max_results=1, lookback_days=10_000)

    assert requests == 1
    assert arxiv_service._cooldown_path.exists()


@pytest.mark.asyncio
async def test_fetch_uses_fresh_disk_cache_without_network(monkeypatch, tmp_path):
    query = "all:world models"
    monkeypatch.setattr(arxiv_service, "_cache_dir", tmp_path)
    monkeypatch.setattr(arxiv_service, "get_settings", lambda: _settings())
    arxiv_service._write_cached_feed(query, 3, SAMPLE_FEED)

    async def no_rss(*_args, **_kwargs):
        return None

    async def no_openalex(*_args, **_kwargs):
        return None

    async def unexpected_request(*_args, **_kwargs):
        raise AssertionError("fresh cache should avoid an arXiv request")

    monkeypatch.setattr(arxiv_service, "_request_with_global_limit", unexpected_request)
    monkeypatch.setattr(arxiv_service, "_fetch_daily_rss", no_rss)
    monkeypatch.setattr(arxiv_service, "_fetch_openalex", no_openalex)

    batch = await fetch_papers_detailed(query, max_results=1, lookback_days=10_000)

    assert batch.source == "cache"
    assert batch.warning is None
    assert [paper.arxiv_id for paper in batch.papers] == ["2609.01234"]
