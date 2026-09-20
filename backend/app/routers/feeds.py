import hashlib
import html
import re
from datetime import UTC
from email.utils import format_datetime
from xml.etree import ElementTree as ET

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import Response
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.database import get_session
from app.models import Analysis, AnalysisStatus, Paper, Topic, TopicPaper, utcnow
from app.services.settings_service import get_schedule_settings

router = APIRouter(tags=["rss"])

ATOM_NAMESPACE = "http://www.w3.org/2005/Atom"
CONTENT_NAMESPACE = "http://purl.org/rss/1.0/modules/content/"
DC_NAMESPACE = "http://purl.org/dc/elements/1.1/"
_INVALID_XML_CHARACTERS = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f]")

ET.register_namespace("atom", ATOM_NAMESPACE)
ET.register_namespace("content", CONTENT_NAMESPACE)
ET.register_namespace("dc", DC_NAMESPACE)


def _clean(value: object | None) -> str:
    return _INVALID_XML_CHARACTERS.sub("", str(value or "")).strip()


def _latest_completed_analysis(paper: Paper) -> Analysis | None:
    return max(
        (analysis for analysis in paper.analyses if analysis.status == AnalysisStatus.completed),
        key=lambda analysis: analysis.id,
        default=None,
    )


def _paper_description(paper: Paper, analysis: Analysis | None) -> str:
    topics = sorted({link.topic.name for link in paper.topic_links})
    authors = ", ".join(_clean(author) for author in paper.authors)
    summary = _clean(analysis.summary if analysis else paper.abstract)
    source_label = "AI 解读" if analysis and analysis.summary else "论文摘要"
    score = ""
    if analysis and analysis.relevance_score is not None:
        score = f"<p><strong>相关性评分：</strong>{analysis.relevance_score:.1f}/10</p>"
    topic_html = (
        f"<p><strong>主题：</strong>{html.escape(', '.join(topics))}</p>" if topics else ""
    )
    return (
        f"<p><strong>作者：</strong>{html.escape(authors)}</p>"
        f"{topic_html}"
        f"<p><strong>{source_label}：</strong></p>"
        f"<p>{html.escape(summary).replace(chr(10), '<br>')}</p>"
        f"{score}"
        f'<p><a href="{html.escape(paper.abs_url, quote=True)}">arXiv 页面</a> · '
        f'<a href="{html.escape(paper.pdf_url, quote=True)}">PDF</a></p>'
    )


def _build_rss(
    papers: list[Paper],
    *,
    title: str,
    site_url: str,
    feed_url: str,
) -> bytes:
    rss = ET.Element("rss", {"version": "2.0"})
    channel = ET.SubElement(rss, "channel")
    ET.SubElement(channel, "title").text = _clean(title)
    ET.SubElement(channel, "link").text = site_url
    ET.SubElement(channel, "description").text = "每日 arXiv 论文与已有科研解读"
    ET.SubElement(channel, "language").text = "zh-CN"
    ET.SubElement(channel, "generator").text = "ArxivLens"
    ET.SubElement(channel, "ttl").text = "30"
    ET.SubElement(
        channel,
        f"{{{ATOM_NAMESPACE}}}link",
        {"href": feed_url, "rel": "self", "type": "application/rss+xml"},
    )
    item_data = [(paper, _latest_completed_analysis(paper)) for paper in papers]
    build_time = max(
        (
            analysis.completed_at
            if analysis and analysis.completed_at
            else paper.first_seen_at
            for paper, analysis in item_data
        ),
        default=utcnow(),
    )
    ET.SubElement(channel, "lastBuildDate").text = format_datetime(
        build_time.astimezone(UTC), usegmt=True
    )

    for paper, analysis in item_data:
        detail_url = f"{site_url.rstrip('/')}/#/paper/{paper.id}"
        item = ET.SubElement(channel, "item")
        ET.SubElement(item, "title").text = _clean(paper.title)
        ET.SubElement(item, "link").text = detail_url
        ET.SubElement(item, "guid", {"isPermaLink": "false"}).text = (
            f"arxiv:{paper.arxiv_id}:v{paper.version}"
        )
        item_time = (
            analysis.completed_at if analysis and analysis.completed_at else paper.first_seen_at
        )
        ET.SubElement(item, "pubDate").text = format_datetime(
            item_time.astimezone(UTC), usegmt=True
        )
        ET.SubElement(item, f"{{{DC_NAMESPACE}}}creator").text = _clean(
            ", ".join(paper.authors)
        )
        for category in paper.categories:
            ET.SubElement(item, "category").text = _clean(category)
        description = _paper_description(paper, analysis)
        ET.SubElement(item, "description").text = description
        ET.SubElement(item, f"{{{CONTENT_NAMESPACE}}}encoded").text = description

    return ET.tostring(rss, encoding="utf-8", xml_declaration=True)


@router.get("/rss.xml", response_class=Response)
async def read_rss_feed(
    request: Request,
    topic_id: int | None = None,
    limit: int = Query(default=50, ge=1, le=100),
    session: AsyncSession = Depends(get_session),
):
    schedule = await get_schedule_settings(session)
    topic = await session.get(Topic, topic_id) if topic_id is not None else None
    if topic_id is not None and topic is None:
        raise HTTPException(status_code=404, detail="Topic not found")

    statement = select(Paper).options(
        selectinload(Paper.topic_links).joinedload(TopicPaper.topic),
        selectinload(Paper.analyses),
    )
    if topic_id is not None:
        statement = statement.where(
            Paper.id.in_(select(TopicPaper.paper_id).where(TopicPaper.topic_id == topic_id))
        )
    statement = statement.order_by(Paper.first_seen_at.desc(), Paper.id.desc()).limit(limit)
    papers = list(await session.scalars(statement))

    feed_url = str(request.url)
    title = f"ArxivLens · {topic.name}" if topic else "ArxivLens · 每日论文"
    xml = _build_rss(
        papers,
        title=title,
        site_url=schedule.public_base_url.rstrip("/"),
        feed_url=feed_url,
    )
    etag = f'"{hashlib.sha256(xml).hexdigest()}"'
    cache_headers = {"Cache-Control": "public, max-age=300", "ETag": etag}
    if request.headers.get("if-none-match") == etag:
        return Response(status_code=304, headers=cache_headers)
    return Response(
        content=xml,
        media_type="application/rss+xml",
        headers=cache_headers,
    )
