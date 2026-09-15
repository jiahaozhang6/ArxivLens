from sqlalchemy import delete

from app.database import SessionLocal
from app.models import Analysis, AnalysisStatus, LLMProfile, Paper, ProtocolType, utcnow
from app.services import analysis_service
from app.services.llm import AnalysisPayload, LLMRequestError
from app.services.model_routing import reset_model_cooldowns


def _payload() -> AnalysisPayload:
    return AnalysisPayload(
        summary="备用模型完成解读",
        research_question="测试自动切换",
        contributions=["自动恢复"],
        methodology="依次尝试启用模型",
        experiments="模拟首选模型异常",
        limitations=["仅测试路由"],
        reading_advice="继续阅读",
        relevance_reason="验证模型容灾",
        keywords=["fallback"],
        novelty_score=7,
        rigor_score=8,
        relevance_score=9,
    )


async def test_execute_analysis_switches_to_default_profile(monkeypatch):
    reset_model_cooldowns()
    now = utcnow()
    suffix = str(now.timestamp())
    async with SessionLocal() as session:
        preferred = LLMProfile(
            name=f"preferred-{suffix}",
            provider="primary-provider",
            protocol=ProtocolType.openai_compatible,
            base_url="https://primary.example.com/v1",
            model="primary-model",
            encrypted_api_key="unused",
            enabled=True,
            is_default=False,
        )
        backup = LLMProfile(
            name=f"000-backup-{suffix}",
            provider="backup-provider",
            protocol=ProtocolType.openai_compatible,
            base_url="https://backup.example.com/v1",
            model="backup-model",
            encrypted_api_key="unused",
            enabled=True,
            is_default=True,
        )
        paper = Paper(
            arxiv_id=f"fallback-{suffix}",
            title="Model fallback test",
            abstract="Test abstract",
            authors=["Test Author"],
            categories=["cs.AI"],
            primary_category="cs.AI",
            published_at=now,
            updated_at=now,
            abs_url="https://arxiv.org/abs/fallback-test",
            pdf_url="https://arxiv.org/pdf/fallback-test",
        )
        session.add_all([preferred, backup, paper])
        await session.flush()
        analysis = Analysis(
            paper_id=paper.id,
            llm_profile_id=preferred.id,
            provider=preferred.provider,
            model=preferred.model,
            status=AnalysisStatus.pending,
        )
        session.add(analysis)
        await session.commit()
        analysis_id = analysis.id
        preferred_id = preferred.id
        backup_id = backup.id
        paper_id = paper.id

    called_profile_ids: list[int] = []

    async def fake_analyze(profile, *_args, **_kwargs):
        called_profile_ids.append(profile.id)
        if profile.id == preferred_id:
            raise LLMRequestError("HTTP 429: rate limit")
        return _payload(), {"summary": "备用模型完成解读"}

    monkeypatch.setattr(analysis_service, "analyze_with_profile", fake_analyze)

    assert await analysis_service.execute_analysis(analysis_id) is True
    assert called_profile_ids == [preferred_id, backup_id]
    async with SessionLocal() as session:
        stored = await session.get(Analysis, analysis_id)
        assert stored is not None
        assert stored.status == AnalysisStatus.completed
        assert stored.llm_profile_id == preferred_id
        assert stored.provider == "backup-provider"
        assert stored.model == "backup-model"
        assert stored.model_routing is not None
        assert stored.model_routing["fallback_used"] is True
        assert stored.model_routing["used"]["profile_id"] == backup_id
        assert "已自动切换" in (stored.error_message or "")

        await session.execute(delete(Paper).where(Paper.id == paper_id))
        await session.execute(
            delete(LLMProfile).where(LLMProfile.id.in_([preferred_id, backup_id]))
        )
        await session.commit()
    reset_model_cooldowns()
