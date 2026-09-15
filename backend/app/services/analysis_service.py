from sqlalchemy import select
from sqlalchemy.orm import joinedload

from app.database import SessionLocal
from app.models import Analysis, AnalysisStatus, LLMProfile, Paper, Topic, utcnow
from app.services.llm import analyze_with_profile
from app.services.pdf import extract_arxiv_text


async def create_analysis(
    paper_id: int,
    topic_id: int | None,
    profile_id: int,
    source_mode: str,
    language: str,
    run_id: int | None = None,
) -> int:
    async with SessionLocal() as session:
        profile = await session.get(LLMProfile, profile_id)
        if profile is None:
            raise ValueError("LLM profile not found")
        paper = await session.get(Paper, paper_id)
        if paper is None:
            raise ValueError("Paper not found")
        analysis = Analysis(
            paper_id=paper_id,
            run_id=run_id,
            topic_id=topic_id,
            llm_profile_id=profile_id,
            provider=profile.provider,
            model=profile.model,
            language=language,
            source_mode=source_mode,
            paper_version=paper.version,
            status=AnalysisStatus.pending,
        )
        session.add(analysis)
        await session.commit()
        await session.refresh(analysis)
        return analysis.id


async def execute_analysis(analysis_id: int) -> bool:
    async with SessionLocal() as session:
        analysis = await session.scalar(
            select(Analysis)
            .where(Analysis.id == analysis_id)
            .options(
                joinedload(Analysis.paper),
                joinedload(Analysis.topic),
                joinedload(Analysis.llm_profile),
            )
        )
        if analysis is None:
            return False
        paper: Paper = analysis.paper
        topic: Topic | None = analysis.topic
        profile: LLMProfile | None = analysis.llm_profile
        if profile is None or not profile.enabled:
            analysis.status = AnalysisStatus.failed
            analysis.error_message = "The selected cloud model profile is missing or disabled"
            analysis.completed_at = utcnow()
            await session.commit()
            return False
        analysis.status = AnalysisStatus.running
        await session.commit()

        source_text = paper.abstract
        actual_source_mode = "abstract"
        full_text_warning: str | None = None
        if analysis.source_mode == "pdf":
            try:
                source_text, actual_source_mode, extraction_errors = await extract_arxiv_text(
                    paper.arxiv_id,
                    paper.version,
                    paper.pdf_url,
                )
                if extraction_errors:
                    full_text_warning = "Full-text fallback used: " + "; ".join(extraction_errors)
            except Exception as exc:  # Full-text failure should not discard the paper.
                full_text_warning = f"Full-text extraction failed; analyzed the abstract instead: {exc}"

        try:
            payload, raw = await analyze_with_profile(
                profile,
                paper,
                topic,
                source_text,
                actual_source_mode,
                analysis.language,
            )
            analysis.status = AnalysisStatus.completed
            analysis.source_mode = actual_source_mode
            analysis.summary = payload.summary
            analysis.research_question = payload.research_question
            analysis.contributions = payload.contributions
            analysis.methodology = payload.methodology
            analysis.experiments = payload.experiments
            analysis.limitations = payload.limitations
            analysis.reading_advice = payload.reading_advice
            analysis.relevance_reason = payload.relevance_reason
            analysis.keywords = payload.keywords
            analysis.novelty_score = payload.novelty_score
            analysis.rigor_score = payload.rigor_score
            analysis.relevance_score = payload.relevance_score
            analysis.raw_response = raw
            analysis.error_message = full_text_warning
            analysis.completed_at = utcnow()
            await session.commit()
            return True
        except Exception as exc:
            analysis.status = AnalysisStatus.failed
            analysis.error_message = str(exc)[:4000]
            analysis.completed_at = utcnow()
            await session.commit()
            return False
