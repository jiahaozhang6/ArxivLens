import asyncio
import hashlib
import html
import json
import os
import random
import re
import time
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from email.utils import parsedate_to_datetime
from pathlib import Path
from urllib.parse import quote
from xml.etree import ElementTree

import httpx
from filelock import FileLock

from app.config import PROJECT_ROOT, get_settings

ARXIV_API_URL = "https://export.arxiv.org/api/query"
ARXIV_RSS_URL = "https://rss.arxiv.org/atom"
OPENALEX_WORKS_URL = "https://api.openalex.org/works"
OPENALEX_ARXIV_SOURCE_ID = "S4306400194"
ATOM = "{http://www.w3.org/2005/Atom}"
ARXIV = "{http://arxiv.org/schemas/atom}"
DC = "{http://purl.org/dc/elements/1.1/}"
_request_lock = asyncio.Lock()
_rate_lock_path = PROJECT_ROOT / "data" / ".arxiv-api-rate.lock"
_rate_state_path = PROJECT_ROOT / "data" / ".arxiv-api-last-request"
_cache_dir = PROJECT_ROOT / "data" / "arxiv-cache"
_rss_cache_dir = PROJECT_ROOT / "data" / "arxiv-rss-cache"
_openalex_cache_dir = PROJECT_ROOT / "data" / "openalex-cache"
_cooldown_path = PROJECT_ROOT / "data" / "arxiv-cache" / "atom-cooldown.json"


@dataclass(slots=True)
class ArxivPaperData:
    arxiv_id: str
    version: int
    title: str
    abstract: str
    authors: list[str]
    categories: list[str]
    primary_category: str | None
    published_at: datetime
    updated_at: datetime
    abs_url: str
    pdf_url: str
    doi: str | None
    journal_ref: str | None
    comment: str | None
    announce_type: str = "new"


@dataclass(frozen=True, slots=True)
class ArxivFetchBatch:
    papers: list[ArxivPaperData]
    source: str
    cached_at: datetime | None = None
    warning: str | None = None
    degraded: bool = False
    retry_recommended: bool = False


@dataclass(frozen=True, slots=True)
class _CachedFeed:
    xml_text: str
    fetched_at: datetime


def _new_rate_lock() -> FileLock:
    # asyncio.to_thread may acquire and release on different executor threads.
    return FileLock(str(_rate_lock_path), thread_local=False)


def _clean_text(value: str | None) -> str:
    return re.sub(r"\s+", " ", value or "").strip()


def _parse_datetime(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _parse_arxiv_id(url: str) -> tuple[str, int]:
    raw_id = url.strip().rstrip("/")
    raw_id = raw_id.removeprefix("oai:arXiv.org:")
    raw_id = raw_id.split("/abs/")[-1]
    raw_id = raw_id.split("/pdf/")[-1].removesuffix(".pdf")
    match = re.search(r"v(\d+)$", raw_id)
    if not match:
        return raw_id, 1
    return raw_id[: match.start()], int(match.group(1))


def parse_atom_feed(xml_text: str) -> list[ArxivPaperData]:
    root = ElementTree.fromstring(xml_text)
    papers: list[ArxivPaperData] = []
    for entry in root.findall(f"{ATOM}entry"):
        entry_id = _clean_text(entry.findtext(f"{ATOM}id"))
        if not entry_id:
            continue
        arxiv_id, version = _parse_arxiv_id(entry_id)
        links = {
            link.attrib.get("title") or link.attrib.get("rel", ""): link.attrib.get("href", "")
            for link in entry.findall(f"{ATOM}link")
        }
        abs_url = links.get("alternate", entry_id)
        pdf_url = links.get("pdf", f"https://arxiv.org/pdf/{arxiv_id}")
        primary = entry.find(f"{ARXIV}primary_category")
        papers.append(
            ArxivPaperData(
                arxiv_id=arxiv_id,
                version=version,
                title=_clean_text(entry.findtext(f"{ATOM}title")),
                abstract=_clean_text(entry.findtext(f"{ATOM}summary")),
                authors=[
                    _clean_text(author.findtext(f"{ATOM}name"))
                    for author in entry.findall(f"{ATOM}author")
                ],
                categories=[
                    category.attrib.get("term", "")
                    for category in entry.findall(f"{ATOM}category")
                    if category.attrib.get("term")
                ],
                primary_category=primary.attrib.get("term") if primary is not None else None,
                published_at=_parse_datetime(entry.findtext(f"{ATOM}published", "")),
                updated_at=_parse_datetime(entry.findtext(f"{ATOM}updated", "")),
                abs_url=abs_url,
                pdf_url=pdf_url,
                doi=_clean_text(entry.findtext(f"{ARXIV}doi")) or None,
                journal_ref=_clean_text(entry.findtext(f"{ARXIV}journal_ref")) or None,
                comment=_clean_text(entry.findtext(f"{ARXIV}comment")) or None,
            )
        )
    return papers


def _clean_daily_atom_summary(value: str | None) -> str:
    cleaned = _clean_text(value)
    parts = re.split(r"\bAbstract:\s*", cleaned, maxsplit=1, flags=re.IGNORECASE)
    return parts[-1] if parts else cleaned


def parse_daily_atom_feed(xml_text: str, default_category: str) -> list[ArxivPaperData]:
    root = ElementTree.fromstring(xml_text)
    papers: list[ArxivPaperData] = []
    for entry in root.findall(f"{ATOM}entry"):
        entry_id = _clean_text(entry.findtext(f"{ATOM}id"))
        if not entry_id:
            continue
        arxiv_id, version = _parse_arxiv_id(entry_id)
        links = {
            link.attrib.get("rel", ""): link.attrib.get("href", "")
            for link in entry.findall(f"{ATOM}link")
        }
        categories = [
            category.attrib.get("term", "")
            for category in entry.findall(f"{ATOM}category")
            if category.attrib.get("term")
        ]
        if default_category not in categories:
            categories.append(default_category)
        authors = [
            _clean_text(author.findtext(f"{ATOM}name"))
            for author in entry.findall(f"{ATOM}author")
            if _clean_text(author.findtext(f"{ATOM}name"))
        ]
        if not authors:
            authors = _split_rss_authors(entry.findtext(f"{DC}creator"))
        published_at = _parse_rss_datetime(entry.findtext(f"{ATOM}published"))
        updated_at = _parse_rss_datetime(entry.findtext(f"{ATOM}updated"))
        announce_type = _clean_text(entry.findtext(f"{ARXIV}announce_type")) or "new"
        abs_url = links.get("alternate") or f"https://arxiv.org/abs/{arxiv_id}"
        version_suffix = f"v{version}" if version > 1 else ""
        papers.append(
            ArxivPaperData(
                arxiv_id=arxiv_id,
                version=version,
                title=_clean_text(entry.findtext(f"{ATOM}title")),
                abstract=_clean_daily_atom_summary(entry.findtext(f"{ATOM}summary")),
                authors=authors,
                categories=categories,
                primary_category=categories[0] if categories else default_category,
                published_at=published_at,
                updated_at=updated_at,
                abs_url=abs_url,
                pdf_url=f"https://arxiv.org/pdf/{arxiv_id}{version_suffix}",
                doi=None,
                journal_ref=None,
                comment=None,
                announce_type=announce_type.casefold(),
            )
        )
    return papers


def _parse_rss_datetime(value: str | None) -> datetime:
    cleaned = _clean_text(value)
    if not cleaned:
        return datetime.now(UTC)
    try:
        parsed = parsedate_to_datetime(cleaned)
    except (TypeError, ValueError, OverflowError):
        parsed = datetime.fromisoformat(cleaned.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def _clean_rss_description(value: str | None) -> str:
    plain = html.unescape(re.sub(r"<[^>]+>", " ", value or ""))
    abstract = re.split(r"\bAbstract:\s*", plain, maxsplit=1, flags=re.IGNORECASE)[-1]
    return _clean_text(abstract)


def _split_rss_authors(value: str | None) -> list[str]:
    cleaned = _clean_text(value)
    if not cleaned:
        return []
    return [
        author.strip()
        for author in re.split(r"\s+and\s+|;\s*|,\s*(?=[A-Z])", cleaned)
        if author.strip()
    ]


def parse_rss_feed(xml_text: str, default_category: str) -> list[ArxivPaperData]:
    root = ElementTree.fromstring(xml_text)
    papers: list[ArxivPaperData] = []
    for item in root.findall(".//item"):
        abs_url = _clean_text(item.findtext("link"))
        if "/abs/" not in abs_url:
            continue
        arxiv_id, version = _parse_arxiv_id(abs_url)
        categories = [
            _clean_text(node.text)
            for node in item.findall("category")
            if _clean_text(node.text)
        ]
        if default_category not in categories:
            categories.append(default_category)
        published_at = _parse_rss_datetime(
            item.findtext(f"{DC}date") or item.findtext("pubDate")
        )
        papers.append(
            ArxivPaperData(
                arxiv_id=arxiv_id,
                version=version,
                title=_clean_text(item.findtext("title")),
                abstract=_clean_rss_description(item.findtext("description")),
                authors=_split_rss_authors(item.findtext(f"{DC}creator")),
                categories=categories,
                primary_category=categories[0] if categories else default_category,
                published_at=published_at,
                updated_at=published_at,
                abs_url=abs_url,
                pdf_url=abs_url.replace("/abs/", "/pdf/"),
                doi=None,
                journal_ref=None,
                comment=None,
            )
        )
    return papers


def _read_last_request_at() -> float:
    try:
        return float(_rate_state_path.read_text(encoding="ascii").strip())
    except (OSError, ValueError):
        return 0.0


def _write_last_request_at(value: float) -> None:
    _rate_state_path.parent.mkdir(parents=True, exist_ok=True)
    temporary = _rate_state_path.with_name(f"{_rate_state_path.name}.{os.getpid()}.tmp")
    temporary.write_text(f"{value:.6f}", encoding="ascii")
    os.replace(temporary, _rate_state_path)


async def _request_with_global_limit(
    client: httpx.AsyncClient,
    params: dict,
    headers: dict,
    url: str = ARXIV_API_URL,
) -> httpx.Response:
    settings = get_settings()
    async with _request_lock:
        _rate_lock_path.parent.mkdir(parents=True, exist_ok=True)
        file_lock = _new_rate_lock()
        await asyncio.to_thread(
            file_lock.acquire,
            timeout=max(settings.http_timeout_seconds * 2, 180),
        )
        try:
            now = time.time()
            last_request_at = _read_last_request_at()
            if last_request_at > now + settings.arxiv_min_interval_seconds:
                last_request_at = 0.0
            wait_for = settings.arxiv_min_interval_seconds - (now - last_request_at)
            if wait_for > 0:
                await asyncio.sleep(wait_for)
            _write_last_request_at(time.time())
            return await client.get(url, params=params, headers=headers)
        finally:
            await asyncio.to_thread(file_lock.release)


def _rss_categories(query: str) -> list[str]:
    return list(
        dict.fromkeys(
            re.findall(r"\bcat:([A-Za-z0-9.-]+)", query, flags=re.IGNORECASE)
        )
    )


def _normalized_search_text(value: str) -> str:
    return _clean_text(re.sub(r"[^\w]+", " ", value.casefold()))


_QUERY_TOKEN_RE = re.compile(
    r'\(|\)|\bANDNOT\b|\bAND\b|\bOR\b|'
    r'\b(?:all|ti|abs|au|cat):(?:"[^"]+"|[^\s()]+)',
    flags=re.IGNORECASE,
)


def _query_tokens(query: str) -> list[str]:
    return [match.group(0) for match in _QUERY_TOKEN_RE.finditer(query)]


def _term_matches(paper: ArxivPaperData, token: str) -> bool:
    field, raw_value = token.split(":", 1)
    value = raw_value.strip().strip('"')
    if field.casefold() == "cat":
        normalized = value.casefold()
        return any(category.casefold() == normalized for category in paper.categories)
    needle = _normalized_search_text(value)
    if not needle:
        return True
    if field.casefold() == "ti":
        haystack = _normalized_search_text(paper.title)
    elif field.casefold() == "abs":
        haystack = _normalized_search_text(paper.abstract)
    elif field.casefold() == "au":
        haystack = _normalized_search_text(" ".join(paper.authors))
    else:
        haystack = _normalized_search_text(
            f"{paper.title} {paper.abstract} {' '.join(paper.authors)}"
        )
    return needle in haystack


class _QueryEvaluator:
    def __init__(self, paper: ArxivPaperData, tokens: list[str]):
        self.paper = paper
        self.tokens = tokens
        self.index = 0

    def evaluate(self) -> bool:
        if not self.tokens:
            return True
        return self._parse_or()

    def _peek(self) -> str | None:
        return self.tokens[self.index] if self.index < len(self.tokens) else None

    def _take(self) -> str:
        token = self.tokens[self.index]
        self.index += 1
        return token

    def _parse_or(self) -> bool:
        value = self._parse_and()
        while (token := self._peek()) is not None and token.casefold() == "or":
            self._take()
            value = self._parse_and() or value
        return value

    def _parse_and(self) -> bool:
        value = self._parse_factor()
        while (token := self._peek()) is not None and token.casefold() in {"and", "andnot"}:
            operator = self._take().casefold()
            other = self._parse_factor()
            value = value and (not other if operator == "andnot" else other)
        return value

    def _parse_factor(self) -> bool:
        token = self._peek()
        if token is None:
            return True
        if token == "(":
            self._take()
            value = self._parse_or()
            if self._peek() == ")":
                self._take()
            return value
        if token == ")":
            return True
        return _term_matches(self.paper, self._take())


def _matches_query(paper: ArxivPaperData, query: str) -> bool:
    return _QueryEvaluator(paper, _query_tokens(query)).evaluate()


def _search_phrases(query: str) -> list[str]:
    phrases: list[str] = []
    for token in _query_tokens(query):
        if ":" not in token:
            continue
        field, value = token.split(":", 1)
        if field.casefold() == "cat":
            continue
        cleaned = value.strip().strip('"')
        if cleaned and cleaned.casefold() not in {item.casefold() for item in phrases}:
            phrases.append(cleaned)
    return phrases


async def _fetch_daily_rss(
    client: httpx.AsyncClient,
    query: str,
    max_results: int,
    lookback_days: int,
    headers: dict,
    include_cross_list: bool,
) -> ArxivFetchBatch | None:
    categories = _rss_categories(query)
    if not categories:
        return None

    settings = get_settings()
    by_id: dict[str, ArxivPaperData] = {}
    successful_categories: list[str] = []
    rss_errors: list[str] = []
    used_stale_cache = False
    for category in categories:
        url = f"{ARXIV_RSS_URL}/{quote(category, safe='.')}"
        cached = await asyncio.to_thread(_read_rss_cache, category)
        now = datetime.now(UTC)
        xml_text: str | None = None
        if cached and now - cached.fetched_at <= timedelta(
            seconds=settings.arxiv_rss_cache_ttl_seconds
        ):
            xml_text = cached.xml_text
        try:
            if xml_text is None:
                response = await client.get(url, headers=headers)
                if response.status_code != 200:
                    raise RuntimeError(_response_error(response))
                xml_text = response.text
                await asyncio.to_thread(_write_rss_cache, category, xml_text)
            successful_categories.append(category)
            for paper in parse_daily_atom_feed(xml_text, category):
                if paper.announce_type == "cross" and not include_cross_list:
                    continue
                if paper.announce_type not in {"new", "cross"}:
                    continue
                existing = by_id.get(paper.arxiv_id)
                if existing is None or paper.version > existing.version:
                    by_id[paper.arxiv_id] = paper
        except (httpx.HTTPError, ElementTree.ParseError, RuntimeError, ValueError) as exc:
            if cached and now - cached.fetched_at <= timedelta(
                hours=settings.arxiv_rss_stale_cache_hours
            ):
                try:
                    used_stale_cache = True
                    successful_categories.append(category)
                    for paper in parse_daily_atom_feed(cached.xml_text, category):
                        if paper.announce_type == "cross" and not include_cross_list:
                            continue
                        if paper.announce_type not in {"new", "cross"}:
                            continue
                        existing = by_id.get(paper.arxiv_id)
                        if existing is None or paper.version > existing.version:
                            by_id[paper.arxiv_id] = paper
                except (ElementTree.ParseError, ValueError):
                    pass
            description = str(exc).strip() or repr(exc)
            rss_errors.append(f"{category}: {type(exc).__name__}: {description}")

    if not successful_categories:
        return None

    cutoff = datetime.now(UTC) - timedelta(days=lookback_days)
    papers = sorted(
        (
            paper
            for paper in by_id.values()
            if paper.published_at >= cutoff and _matches_query(paper, query)
        ),
        key=lambda paper: paper.published_at,
        reverse=True,
    )[:max_results]
    warning = None
    if rss_errors:
        warning = f"部分 arXiv RSS 分类读取失败：{'；'.join(rss_errors[:2])}"
        if used_stale_cache:
            warning += "；已使用可用的历史分类缓存"
    return ArxivFetchBatch(
        papers=papers,
        source="rss_cache" if used_stale_cache else "rss",
        cached_at=datetime.now(UTC),
        warning=warning,
        degraded=bool(rss_errors),
        retry_recommended=bool(rss_errors),
    )


def _cache_key(query: str, request_limit: int) -> str:
    value = json.dumps(
        {"query": query, "max_results": request_limit},
        ensure_ascii=False,
        sort_keys=True,
    )
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _cache_path(query: str, request_limit: int) -> Path:
    return _cache_dir / f"{_cache_key(query, request_limit)}.json"


def _read_cached_feed(query: str, request_limit: int) -> _CachedFeed | None:
    path = _cache_path(query, request_limit)
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        fetched_at = datetime.fromisoformat(payload["fetched_at"])
        if fetched_at.tzinfo is None:
            fetched_at = fetched_at.replace(tzinfo=UTC)
        return _CachedFeed(xml_text=payload["xml_text"], fetched_at=fetched_at.astimezone(UTC))
    except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError):
        return None


def _write_cached_feed(query: str, request_limit: int, xml_text: str) -> datetime:
    fetched_at = datetime.now(UTC)
    path = _cache_path(query, request_limit)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f"{path.name}.{os.getpid()}.tmp")
    temporary.write_text(
        json.dumps(
            {"fetched_at": fetched_at.isoformat(), "xml_text": xml_text},
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    os.replace(temporary, path)
    return fetched_at


def _rss_cache_path(category: str) -> Path:
    safe_category = re.sub(r"[^A-Za-z0-9.-]+", "_", category)
    return _rss_cache_dir / f"{safe_category}.json"


def _read_xml_cache(path: Path) -> _CachedFeed | None:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        fetched_at = datetime.fromisoformat(payload["fetched_at"])
        if fetched_at.tzinfo is None:
            fetched_at = fetched_at.replace(tzinfo=UTC)
        return _CachedFeed(
            xml_text=payload["xml_text"],
            fetched_at=fetched_at.astimezone(UTC),
        )
    except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError):
        return None


def _write_xml_cache(path: Path, xml_text: str) -> datetime:
    fetched_at = datetime.now(UTC)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f"{path.name}.{os.getpid()}.tmp")
    temporary.write_text(
        json.dumps(
            {"fetched_at": fetched_at.isoformat(), "xml_text": xml_text},
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    os.replace(temporary, path)
    return fetched_at


def _read_rss_cache(category: str) -> _CachedFeed | None:
    return _read_xml_cache(_rss_cache_path(category))


def _write_rss_cache(category: str, xml_text: str) -> datetime:
    return _write_xml_cache(_rss_cache_path(category), xml_text)


def _read_atom_cooldown() -> tuple[datetime | None, int, str | None]:
    try:
        payload = json.loads(_cooldown_path.read_text(encoding="utf-8"))
        until = datetime.fromisoformat(payload["until"])
        if until.tzinfo is None:
            until = until.replace(tzinfo=UTC)
        return until.astimezone(UTC), int(payload.get("strikes", 1)), payload.get("error")
    except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError):
        return None, 0, None


def _write_atom_cooldown(error: str) -> datetime:
    settings = get_settings()
    previous_until, previous_strikes, _ = _read_atom_cooldown()
    now = datetime.now(UTC)
    if previous_until is not None and previous_until > now:
        strikes = previous_strikes + 1
    else:
        strikes = max(previous_strikes, 0) + 1
    hours = min(
        settings.arxiv_atom_cooldown_base_hours * (2 ** (strikes - 1)),
        settings.arxiv_atom_cooldown_max_hours,
    )
    until = now + timedelta(hours=hours)
    _cooldown_path.parent.mkdir(parents=True, exist_ok=True)
    temporary = _cooldown_path.with_name(f"{_cooldown_path.name}.{os.getpid()}.tmp")
    temporary.write_text(
        json.dumps(
            {"until": until.isoformat(), "strikes": strikes, "error": error},
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    os.replace(temporary, _cooldown_path)
    return until


def _clear_atom_cooldown() -> None:
    try:
        _cooldown_path.unlink()
    except FileNotFoundError:
        pass


def _openalex_abstract(inverted_index: dict | None) -> str:
    if not inverted_index:
        return ""
    positions: list[tuple[int, str]] = []
    for word, offsets in inverted_index.items():
        for offset in offsets or []:
            positions.append((int(offset), str(word)))
    return " ".join(word for _, word in sorted(positions))


def _openalex_arxiv_id(work: dict) -> tuple[str, int] | None:
    for location in work.get("locations") or []:
        landing_page = str(location.get("landing_page_url") or "")
        if "arxiv.org/abs/" in landing_page:
            return _parse_arxiv_id(landing_page)
    doi = str(work.get("doi") or "")
    match = re.search(r"10\.48550/arxiv\.([^/?#]+)", doi, flags=re.IGNORECASE)
    return _parse_arxiv_id(match.group(1)) if match else None


def _parse_openalex_work(work: dict, categories: list[str]) -> ArxivPaperData | None:
    identity = _openalex_arxiv_id(work)
    publication_date = work.get("publication_date")
    if identity is None or not publication_date:
        return None
    arxiv_id, version = identity
    published_at = datetime.fromisoformat(str(publication_date)).replace(tzinfo=UTC)
    authors = [
        _clean_text((authorship.get("author") or {}).get("display_name"))
        for authorship in work.get("authorships") or []
    ]
    authors = [author for author in authors if author]
    title = _clean_text(work.get("display_name") or work.get("title"))
    abstract = _clean_text(_openalex_abstract(work.get("abstract_inverted_index")))
    if not title:
        return None
    return ArxivPaperData(
        arxiv_id=arxiv_id,
        version=version,
        title=title,
        abstract=abstract,
        authors=authors,
        categories=categories,
        primary_category=categories[0] if categories else None,
        published_at=published_at,
        updated_at=published_at,
        abs_url=f"https://arxiv.org/abs/{arxiv_id}",
        pdf_url=f"https://arxiv.org/pdf/{arxiv_id}",
        doi=str(work.get("doi") or "").removeprefix("https://doi.org/") or None,
        journal_ref=None,
        comment=None,
        announce_type="fallback",
    )


def _paper_to_payload(paper: ArxivPaperData) -> dict:
    return {
        "arxiv_id": paper.arxiv_id,
        "version": paper.version,
        "title": paper.title,
        "abstract": paper.abstract,
        "authors": paper.authors,
        "categories": paper.categories,
        "primary_category": paper.primary_category,
        "published_at": paper.published_at.isoformat(),
        "updated_at": paper.updated_at.isoformat(),
        "abs_url": paper.abs_url,
        "pdf_url": paper.pdf_url,
        "doi": paper.doi,
        "journal_ref": paper.journal_ref,
        "comment": paper.comment,
        "announce_type": paper.announce_type,
    }


def _paper_from_payload(payload: dict) -> ArxivPaperData:
    return ArxivPaperData(
        arxiv_id=str(payload["arxiv_id"]),
        version=int(payload.get("version", 1)),
        title=str(payload.get("title", "")),
        abstract=str(payload.get("abstract", "")),
        authors=list(payload.get("authors") or []),
        categories=list(payload.get("categories") or []),
        primary_category=payload.get("primary_category"),
        published_at=_parse_datetime(str(payload["published_at"])),
        updated_at=_parse_datetime(str(payload["updated_at"])),
        abs_url=str(payload["abs_url"]),
        pdf_url=str(payload["pdf_url"]),
        doi=payload.get("doi"),
        journal_ref=payload.get("journal_ref"),
        comment=payload.get("comment"),
        announce_type=str(payload.get("announce_type", "fallback")),
    )


def _openalex_cache_path(query: str, max_results: int, lookback_days: int) -> Path:
    value = json.dumps(
        {"query": query, "max_results": max_results, "lookback_days": lookback_days},
        ensure_ascii=False,
        sort_keys=True,
    )
    return _openalex_cache_dir / f"{hashlib.sha256(value.encode('utf-8')).hexdigest()}.json"


def _read_openalex_cache(
    query: str,
    max_results: int,
    lookback_days: int,
) -> tuple[list[ArxivPaperData], datetime] | None:
    path = _openalex_cache_path(query, max_results, lookback_days)
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        fetched_at = _parse_datetime(payload["fetched_at"])
        return [_paper_from_payload(item) for item in payload["papers"]], fetched_at
    except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError):
        return None


def _write_openalex_cache(
    query: str,
    max_results: int,
    lookback_days: int,
    papers: list[ArxivPaperData],
) -> datetime:
    path = _openalex_cache_path(query, max_results, lookback_days)
    path.parent.mkdir(parents=True, exist_ok=True)
    fetched_at = datetime.now(UTC)
    temporary = path.with_name(f"{path.name}.{os.getpid()}.tmp")
    temporary.write_text(
        json.dumps(
            {
                "fetched_at": fetched_at.isoformat(),
                "papers": [_paper_to_payload(paper) for paper in papers],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    os.replace(temporary, path)
    return fetched_at


async def _fetch_openalex(
    client: httpx.AsyncClient,
    query: str,
    max_results: int,
    lookback_days: int,
    headers: dict,
) -> ArxivFetchBatch | None:
    settings = get_settings()
    if not settings.openalex_enabled:
        return None
    categories = _rss_categories(query)
    is_rss_fallback = bool(categories)
    cached = await asyncio.to_thread(
        _read_openalex_cache,
        query,
        max_results,
        lookback_days,
    )
    now = datetime.now(UTC)
    if cached and now - cached[1] <= timedelta(seconds=settings.openalex_cache_ttl_seconds):
        return ArxivFetchBatch(
            papers=cached[0],
            source="openalex_cache",
            cached_at=cached[1],
            warning=(
                "arXiv RSS 不可用，已使用 OpenAlex 检索缓存"
                if is_rss_fallback
                else "已使用 OpenAlex 检索缓存"
            ),
            degraded=is_rss_fallback,
            retry_recommended=is_rss_fallback,
        )
    phrases = _search_phrases(query)
    if not phrases:
        return None
    cutoff = (datetime.now(UTC) - timedelta(days=lookback_days)).date().isoformat()
    today = datetime.now(UTC).date().isoformat()
    by_id: dict[str, ArxivPaperData] = {}
    errors: list[str] = []
    for phrase in phrases[: settings.openalex_max_queries_per_topic]:
        params = {
            "search": phrase,
            "filter": (
                f"from_publication_date:{cutoff},to_publication_date:{today},"
                f"locations.source.id:{OPENALEX_ARXIV_SOURCE_ID}"
            ),
            "per-page": min(max(max_results * 2, 10), 100),
            "mailto": settings.arxiv_contact_email,
        }
        try:
            response = await client.get(OPENALEX_WORKS_URL, params=params, headers=headers)
            if response.status_code != 200:
                errors.append(_response_error(response))
                continue
            for work in response.json().get("results", []):
                paper = _parse_openalex_work(work, categories)
                if paper is None or not _matches_query(paper, query):
                    continue
                existing = by_id.get(paper.arxiv_id)
                if existing is None or paper.version > existing.version:
                    by_id[paper.arxiv_id] = paper
        except (httpx.HTTPError, ValueError, TypeError, json.JSONDecodeError) as exc:
            errors.append(f"{type(exc).__name__}: {exc}")
    if not by_id and errors:
        if cached and now - cached[1] <= timedelta(hours=settings.openalex_stale_cache_hours):
            return ArxivFetchBatch(
                papers=cached[0],
                source="openalex_stale_cache",
                cached_at=cached[1],
                warning=f"OpenAlex 当前不可用（{errors[0]}），已使用历史缓存",
                degraded=is_rss_fallback,
                retry_recommended=is_rss_fallback,
            )
        return None
    papers = sorted(by_id.values(), key=lambda paper: paper.published_at, reverse=True)
    papers = papers[:max_results]
    fetched_at = await asyncio.to_thread(
        _write_openalex_cache,
        query,
        max_results,
        lookback_days,
        papers,
    )
    warning = (
        "arXiv RSS 不可用，已使用 OpenAlex 恢复检索"
        if is_rss_fallback
        else "未指定 arXiv 分类，已使用 OpenAlex 全库检索"
    )
    if errors:
        warning += f"；部分查询失败：{errors[0]}"
    return ArxivFetchBatch(
        papers=papers,
        source="openalex",
        cached_at=fetched_at,
        warning=warning,
        degraded=is_rss_fallback,
        retry_recommended=is_rss_fallback,
    )


def _recent_papers(xml_text: str, lookback_days: int, max_results: int) -> list[ArxivPaperData]:
    cutoff = datetime.now(UTC) - timedelta(days=lookback_days)
    return [
        paper for paper in parse_atom_feed(xml_text) if paper.published_at >= cutoff
    ][:max_results]


def _response_error(response: httpx.Response) -> str:
    body = _clean_text(response.text)[:240]
    reason = response.reason_phrase or "HTTP error"
    detail = f"HTTP {response.status_code} {reason}"
    return f"{detail}: {body}" if body else detail


def _retry_delay(response: httpx.Response | None, attempt: int) -> float:
    settings = get_settings()
    retry_after: float | None = None
    if response is not None:
        value = response.headers.get("retry-after", "").strip()
        if value:
            try:
                retry_after = float(value)
            except ValueError:
                try:
                    retry_at = parsedate_to_datetime(value)
                    if retry_at.tzinfo is None:
                        retry_at = retry_at.replace(tzinfo=UTC)
                    retry_after = max((retry_at - datetime.now(UTC)).total_seconds(), 0)
                except (TypeError, ValueError, OverflowError):
                    retry_after = None
    base = retry_after if retry_after is not None else settings.arxiv_retry_base_seconds * 2**attempt
    jitter = random.uniform(0, min(2.0, base * 0.1)) if base > 0 else 0.0
    return min(base + jitter, settings.arxiv_retry_max_seconds)


def _cached_batch(
    cached: _CachedFeed,
    lookback_days: int,
    max_results: int,
    *,
    stale: bool,
    warning: str | None = None,
) -> ArxivFetchBatch:
    return ArxivFetchBatch(
        papers=_recent_papers(cached.xml_text, lookback_days, max_results),
        source="stale_cache" if stale else "cache",
        cached_at=cached.fetched_at,
        warning=warning,
        degraded=stale,
        retry_recommended=stale,
    )


def _merge_fallback_batches(
    primary_batch: ArxivFetchBatch,
    fallback_batch: ArxivFetchBatch,
    max_results: int,
) -> ArxivFetchBatch:
    by_id = {paper.arxiv_id: paper for paper in primary_batch.papers}
    for paper in fallback_batch.papers:
        existing = by_id.get(paper.arxiv_id)
        if existing is None or paper.version > existing.version:
            by_id[paper.arxiv_id] = paper
    papers = sorted(
        by_id.values(),
        key=lambda paper: paper.published_at,
        reverse=True,
    )[:max_results]
    warning_parts = [primary_batch.warning, fallback_batch.warning]
    return ArxivFetchBatch(
        papers=papers,
        source=f"{primary_batch.source}_{fallback_batch.source}",
        cached_at=max(
            value
            for value in (primary_batch.cached_at, fallback_batch.cached_at)
            if value is not None
        ),
        warning="；".join(part for part in warning_parts if part) or None,
        degraded=primary_batch.degraded or fallback_batch.degraded,
        retry_recommended=(
            primary_batch.retry_recommended or fallback_batch.retry_recommended
        ),
    )


async def fetch_papers_detailed(
    query: str,
    max_results: int,
    lookback_days: int,
    include_cross_list: bool = False,
) -> ArxivFetchBatch:
    settings = get_settings()
    request_limit = min(max(max_results * 3, max_results), 100)
    cached = await asyncio.to_thread(_read_cached_feed, query, request_limit)
    now = datetime.now(UTC)
    headers = {
        "User-Agent": f"arxiv-research-digest/0.1 ({settings.arxiv_contact_email})",
        "Accept": "application/atom+xml",
    }
    async with httpx.AsyncClient(
        timeout=settings.http_timeout_seconds,
        follow_redirects=True,
    ) as client:
        rss_batch = await _fetch_daily_rss(
            client,
            query,
            max_results,
            lookback_days,
            headers,
            include_cross_list,
        )
        if rss_batch is not None and not rss_batch.degraded:
            return rss_batch

        openalex_batch = await _fetch_openalex(
            client,
            query,
            max_results,
            lookback_days,
            {**headers, "Accept": "application/json"},
        )
        if rss_batch is not None and openalex_batch is not None:
            return _merge_fallback_batches(rss_batch, openalex_batch, max_results)
        if rss_batch is not None:
            return rss_batch
        if openalex_batch is not None:
            return openalex_batch

        if cached and now - cached.fetched_at <= timedelta(
            seconds=settings.arxiv_cache_ttl_seconds
        ):
            return _cached_batch(cached, lookback_days, max_results, stale=False)

        cooldown_until, _, cooldown_error = await asyncio.to_thread(_read_atom_cooldown)
        if cooldown_until is not None and cooldown_until > now:
            warning = (
                "arXiv Atom API 正处于限流冷却，冷却截止 "
                f"{cooldown_until.strftime('%Y-%m-%d %H:%M UTC')}"
            )
            if cooldown_error:
                warning += f"（{cooldown_error}）"
            if cached and now - cached.fetched_at <= timedelta(
                hours=settings.arxiv_stale_cache_hours
            ):
                return _cached_batch(
                    cached,
                    lookback_days,
                    max_results,
                    stale=True,
                    warning=warning + "；已使用历史查询缓存",
                )
            raise RuntimeError(warning)

        params = {
            "search_query": query,
            "start": 0,
            "max_results": request_limit,
            "sortBy": "submittedDate",
            "sortOrder": "descending",
        }
        last_error = "unknown arXiv error"
        attempts = max(settings.arxiv_retry_attempts, 1)
        for attempt in range(attempts):
            response: httpx.Response | None = None
            try:
                response = await _request_with_global_limit(client, params, headers)
                if response.status_code == 200:
                    papers = _recent_papers(response.text, lookback_days, max_results)
                    fetched_at = await asyncio.to_thread(
                        _write_cached_feed,
                        query,
                        request_limit,
                        response.text,
                    )
                    await asyncio.to_thread(_clear_atom_cooldown)
                    return ArxivFetchBatch(
                        papers=papers,
                        source="live",
                        cached_at=fetched_at,
                    )

                last_error = _response_error(response)
                if response.status_code == 429:
                    cooldown_until = await asyncio.to_thread(
                        _write_atom_cooldown,
                        last_error,
                    )
                    last_error += (
                        "; Atom API 已进入持久冷却，截止 "
                        f"{cooldown_until.strftime('%Y-%m-%d %H:%M UTC')}"
                    )
                    break
                retryable = response.status_code == 429 or response.status_code >= 500
                if not retryable:
                    raise RuntimeError(f"arXiv rejected the query: {last_error}")
            except (httpx.HTTPError, ElementTree.ParseError, ValueError) as exc:
                description = str(exc).strip() or repr(exc)
                last_error = f"{type(exc).__name__}: {description}"

            if attempt < attempts - 1:
                await asyncio.sleep(_retry_delay(response, attempt))

        if cached and now - cached.fetched_at <= timedelta(hours=settings.arxiv_stale_cache_hours):
            warning = (
                f"arXiv 当前不可用（{last_error}），已使用 "
                f"{cached.fetched_at.astimezone(UTC).strftime('%Y-%m-%d %H:%M UTC')} 的缓存结果"
            )
            cached_batch = _cached_batch(
                cached,
                lookback_days,
                max_results,
                stale=True,
                warning=warning,
            )
            return cached_batch

    raise RuntimeError(f"arXiv request failed after {attempts} attempts: {last_error}")


async def fetch_papers(
    query: str,
    max_results: int,
    lookback_days: int,
    include_cross_list: bool = False,
) -> list[ArxivPaperData]:
    return (
        await fetch_papers_detailed(
            query,
            max_results,
            lookback_days,
            include_cross_list=include_cross_list,
        )
    ).papers
