from datetime import UTC, datetime, time, timedelta
from email.message import EmailMessage
from html import escape
from zoneinfo import ZoneInfo

import aiosmtplib
from sqlalchemy import select
from sqlalchemy.orm import joinedload

from app.database import SessionLocal
from app.models import Analysis, AnalysisStatus, EmailSettings
from app.security import decrypt_secret
from app.services.network_time import network_clock
from app.services.settings_service import get_email_settings, get_schedule_settings


def _score(value: float | None) -> str:
    return "-" if value is None else f"{value:.1f}"


def _build_digest_html(analyses: list[Analysis], base_url: str) -> str:
    sections: list[str] = []
    for analysis in analyses:
        paper = analysis.paper
        topic_name = analysis.topic.name if analysis.topic else "Uncategorized"
        contributions = "".join(
            f"<li>{escape(str(item))}</li>" for item in (analysis.contributions or [])[:5]
        )
        detail_url = f"{base_url.rstrip('/')}/?paper={paper.id}"
        sections.append(
            f"""
            <article style="border-top:1px solid #d9dee7;padding:20px 0;">
              <div style="font-size:13px;color:#526173;">{escape(topic_name)} · {escape(paper.primary_category or "")}</div>
              <h2 style="font-size:19px;line-height:1.35;margin:7px 0;">
                <a href="{escape(detail_url)}" style="color:#173d6b;text-decoration:none;">{escape(paper.title)}</a>
              </h2>
              <p style="color:#667085;font-size:13px;margin:0 0 12px;">{escape(", ".join(paper.authors[:8]))}</p>
              <p style="line-height:1.65;margin:0 0 12px;">{escape(analysis.summary or "")}</p>
              <ul style="line-height:1.55;margin:0 0 12px;padding-left:22px;">{contributions}</ul>
              <div style="font-size:13px;color:#475467;">Novelty {_score(analysis.novelty_score)} · Rigor {_score(analysis.rigor_score)} · Relevance {_score(analysis.relevance_score)}</div>
            </article>
            """
        )
    return f"""<!doctype html>
    <html><body style="margin:0;background:#f4f6f8;font-family:Arial,sans-serif;color:#1c2733;">
      <main style="max-width:760px;margin:0 auto;background:white;padding:28px 34px;">
        <h1 style="font-size:24px;margin:0 0 4px;">arXiv Daily Research Digest</h1>
        <p style="color:#667085;margin:0 0 20px;">{len(analyses)} papers analyzed today</p>
        {"".join(sections)}
      </main>
    </body></html>"""


def _build_digest_text(analyses: list[Analysis], base_url: str) -> str:
    blocks = [f"arXiv Daily Research Digest - {len(analyses)} papers\n"]
    for analysis in analyses:
        paper = analysis.paper
        blocks.append(
            "\n".join(
                [
                    f"[{analysis.topic.name if analysis.topic else 'Uncategorized'}] {paper.title}",
                    f"Authors: {', '.join(paper.authors[:8])}",
                    analysis.summary or "",
                    f"Scores N/R/Q: {_score(analysis.novelty_score)} / {_score(analysis.relevance_score)} / {_score(analysis.rigor_score)}",
                    f"{base_url.rstrip('/')}/?paper={paper.id}",
                ]
            )
        )
    return "\n\n".join(blocks)


async def _send_message(config: EmailSettings, message: EmailMessage) -> None:
    password = decrypt_secret(config.encrypted_password)
    await aiosmtplib.send(
        message,
        hostname=config.smtp_host,
        port=config.smtp_port,
        username=config.username or None,
        password=password,
        use_tls=config.security == "ssl",
        start_tls=config.security == "starttls",
        timeout=60,
    )


async def _deliver_digest(
    config: EmailSettings,
    analyses: list[Analysis],
    base_url: str,
    subject: str,
) -> None:
    message = EmailMessage()
    message["Subject"] = subject
    message["From"] = f"{config.from_name} <{config.from_email}>"
    message["To"] = ", ".join(config.recipients)
    message.set_content(_build_digest_text(analyses, base_url))
    message.add_alternative(_build_digest_html(analyses, base_url), subtype="html")
    await _send_message(config, message)


async def send_digest(analysis_ids: list[int]) -> int:
    if not analysis_ids:
        return 0
    async with SessionLocal() as session:
        email = await get_email_settings(session)
        schedule = await get_schedule_settings(session)
        if not email.enabled or not email.recipients:
            return 0
        rows = list(
            (
                await session.scalars(
                    select(Analysis)
                    .where(
                        Analysis.id.in_(analysis_ids),
                        Analysis.status == AnalysisStatus.completed,
                    )
                    .options(joinedload(Analysis.paper), joinedload(Analysis.topic))
                    .order_by(Analysis.completed_at.desc(), Analysis.id.desc())
                )
            ).unique()
        )
        latest_by_paper: dict[int, Analysis] = {}
        for analysis in rows:
            latest_by_paper.setdefault(analysis.paper_id, analysis)
        analyses = sorted(
            latest_by_paper.values(),
            key=lambda item: (
                item.relevance_score is not None,
                item.relevance_score if item.relevance_score is not None else 0,
            ),
            reverse=True,
        )
        if not analyses:
            return 0
        await _deliver_digest(
            email,
            analyses,
            schedule.public_base_url,
            f"{email.subject_prefix} {len(analyses)} papers",
        )
        return len(email.recipients)


async def send_today_digest() -> dict[str, str | int]:
    await network_clock.sync()
    async with SessionLocal() as session:
        email = await get_email_settings(session)
        schedule = await get_schedule_settings(session)
        if not email.enabled:
            raise ValueError("请先在“计划与邮件”中启用邮件摘要")
        if not email.smtp_host or not email.from_email or not email.recipients:
            raise ValueError("请先配置 SMTP 服务器、发件邮箱和至少一个收件邮箱")
        if email.username and not decrypt_secret(email.encrypted_password):
            raise ValueError("请先保存 SMTP 密码或授权码")

        timezone = ZoneInfo(schedule.timezone)
        local_day = network_clock.utcnow().astimezone(timezone).date()
        start_local = datetime.combine(local_day, time.min, tzinfo=timezone)
        end_local = datetime.combine(local_day + timedelta(days=1), time.min, tzinfo=timezone)
        rows = list(
            (
                await session.scalars(
                    select(Analysis)
                    .where(
                        Analysis.status == AnalysisStatus.completed,
                        Analysis.completed_at >= start_local.astimezone(UTC),
                        Analysis.completed_at < end_local.astimezone(UTC),
                    )
                    .options(joinedload(Analysis.paper), joinedload(Analysis.topic))
                    .order_by(Analysis.completed_at.desc(), Analysis.id.desc())
                )
            ).unique()
        )
        latest_by_paper: dict[int, Analysis] = {}
        for analysis in rows:
            latest_by_paper.setdefault(analysis.paper_id, analysis)
        analyses = sorted(
            latest_by_paper.values(),
            key=lambda item: (
                item.relevance_score is not None,
                item.relevance_score if item.relevance_score is not None else 0,
            ),
            reverse=True,
        )
        if not analyses:
            raise ValueError(f"{local_day.isoformat()} 没有已完成的论文解读")

        await _deliver_digest(
            email,
            analyses,
            schedule.public_base_url,
            f"{email.subject_prefix} {local_day.isoformat()} - {len(analyses)} papers",
        )
        return {
            "day": local_day.isoformat(),
            "papers": len(analyses),
            "recipients": len(email.recipients),
        }


async def send_test_email(email: EmailSettings) -> None:
    if not email.smtp_host or not email.from_email or not email.recipients:
        raise ValueError("SMTP host, sender, and at least one recipient are required")
    if email.smtp_host.strip().casefold() == "smtp.qq.com" and (
        not email.username or "@" not in email.username
    ):
        raise ValueError("QQ 邮箱的 SMTP 用户名应填写完整邮箱地址")
    if email.username and not decrypt_secret(email.encrypted_password):
        raise ValueError("SMTP password or authorization code is required")
    message = EmailMessage()
    message["Subject"] = f"{email.subject_prefix} connection test"
    message["From"] = f"{email.from_name} <{email.from_email}>"
    message["To"] = ", ".join(email.recipients)
    message.set_content("Your arXiv Research Digest email configuration is working.")
    await _send_message(email, message)
