import asyncio
from xml.etree import ElementTree as ET

from fastapi.testclient import TestClient
from sqlalchemy import delete

from app.database import SessionLocal
from app.main import app
from app.models import Analysis, AnalysisStatus, Paper, Topic, TopicPaper, utcnow


async def _seed_rss_paper() -> tuple[int, int]:
    now = utcnow()
    async with SessionLocal() as session:
        topic = Topic(
            name=f"RSS test topic {now.timestamp()}",
            query='cat:cs.AI AND all:"rss test"',
        )
        paper = Paper(
            arxiv_id=f"rss-test-{now.timestamp()}",
            title="RSS research paper",
            abstract="Public abstract for the RSS feed.",
            authors=["RSS Author"],
            categories=["cs.AI"],
            primary_category="cs.AI",
            published_at=now,
            updated_at=now,
            first_seen_at=now,
            last_seen_at=now,
            abs_url="https://arxiv.org/abs/rss-test",
            pdf_url="https://arxiv.org/pdf/rss-test",
            personal_notes="must-not-appear-in-rss",
            user_tags=["private-rss-tag"],
        )
        session.add_all([topic, paper])
        await session.flush()
        session.add(TopicPaper(topic_id=topic.id, paper_id=paper.id, discovered_at=now))
        session.add(
            Analysis(
                paper_id=paper.id,
                topic_id=topic.id,
                provider="test",
                model="test-model",
                status=AnalysisStatus.completed,
                summary="Completed RSS analysis.\x01",
                relevance_score=8.5,
                completed_at=now,
            )
        )
        await session.commit()
        return topic.id, paper.id


async def _delete_rss_records(topic_id: int, paper_id: int) -> None:
    async with SessionLocal() as session:
        await session.execute(delete(Paper).where(Paper.id == paper_id))
        await session.execute(delete(Topic).where(Topic.id == topic_id))
        await session.commit()


def test_rss_feed_is_public_filtered_and_excludes_private_fields():
    with TestClient(app) as anonymous:
        topic_id, paper_id = asyncio.run(_seed_rss_paper())
        try:
            response = anonymous.get(f"/rss.xml?topic_id={topic_id}&limit=10")
            assert response.status_code == 200, response.text
            assert response.headers["content-type"].startswith("application/rss+xml")
            assert response.headers["cache-control"] == "public, max-age=300"
            assert response.headers["etag"]

            root = ET.fromstring(response.content)
            assert root.tag == "rss"
            channel = root.find("channel")
            assert channel is not None
            assert "RSS test topic" in (channel.findtext("title") or "")
            items = channel.findall("item")
            assert len(items) == 1
            assert items[0].findtext("title") == "RSS research paper"
            assert items[0].findtext("guid")
            description = items[0].findtext("description") or ""
            assert "Completed RSS analysis." in description
            assert "8.5/10" in description
            assert "must-not-appear-in-rss" not in response.text
            assert "private-rss-tag" not in response.text
            assert f"/#/paper/{paper_id}" in response.text
            assert "\x01" not in response.text

            cached = anonymous.get(
                f"/rss.xml?topic_id={topic_id}&limit=10",
                headers={"If-None-Match": response.headers["etag"]},
            )
            assert cached.status_code == 304
            assert not cached.content

            assert anonymous.get("/rss.xml?topic_id=999999999").status_code == 404
        finally:
            asyncio.run(_delete_rss_records(topic_id, paper_id))
