from __future__ import annotations

from sqlalchemy import update

from app.database import SessionLocal
from app.models import Analysis, ChatMessageStatus, Paper, PaperChatMessage, utcnow

CHAT_SYSTEM_PROMPT = """你是一名严谨、高效的人工智能科研阅读助手。
只能依据提供的论文上下文和对话记录回答，不得编造论文中没有出现的数据、实验结果、公式或结论。
默认使用中文，保留必要的英文术语。先直接回答问题，再给出简短的依据和科研判断。
引用摘要中的信息时标记 [摘要]，引用已有结构化解读时标记 [已有解读]。
如果现有上下文不足以回答，明确说明需要阅读全文或查看具体章节，不要猜测。
回答适合博士研究者阅读，避免空泛介绍，优先讨论技术机制、实验可信度、可复现性和研究启发。
"""


def _analysis_context(analysis: Analysis | None) -> str:
    if analysis is None or analysis.status != "completed":
        return "尚无可用的结构化解读。"
    contributions = "\n".join(f"- {item}" for item in analysis.contributions or [])
    limitations = "\n".join(f"- {item}" for item in analysis.limitations or [])
    return f"""摘要结论：{analysis.summary or '未提供'}
研究问题：{analysis.research_question or '未提供'}
主要贡献：
{contributions or '- 未提供'}
方法：{analysis.methodology or '未提供'}
实验与证据：{analysis.experiments or '未提供'}
局限：
{limitations or '- 未提供'}
阅读建议：{analysis.reading_advice or '未提供'}"""


def build_paper_chat_prompt(paper: Paper, analysis: Analysis | None) -> str:
    return f"""{CHAT_SYSTEM_PROMPT}

当前论文：
标题：{paper.title}
作者：{', '.join(paper.authors)}
arXiv：{paper.arxiv_id}v{paper.version}
类别：{', '.join(paper.categories)}

[摘要]
{paper.abstract}

[已有解读]
{_analysis_context(analysis)}
"""


def select_chat_history(
    messages: list[PaperChatMessage],
    *,
    max_messages: int,
    max_chars: int,
) -> list[dict[str, str]]:
    selected: list[dict[str, str]] = []
    used_chars = 0
    for message in reversed(messages):
        if message.role not in {"user", "assistant"}:
            continue
        if message.role == "assistant" and message.status != "completed":
            continue
        content = message.content.strip()
        if not content:
            continue
        if selected and used_chars + len(content) > max_chars:
            break
        selected.append({"role": message.role, "content": content[:max_chars]})
        used_chars += len(content)
        if len(selected) >= max_messages:
            break
    selected.reverse()
    return selected


async def recover_interrupted_chats() -> int:
    async with SessionLocal() as session:
        result = await session.execute(
            update(PaperChatMessage)
            .where(PaperChatMessage.status == ChatMessageStatus.streaming)
            .values(
                status=ChatMessageStatus.cancelled,
                error_message="服务重启中断了本次生成，请重新发送问题",
                completed_at=utcnow(),
            )
        )
        await session.commit()
        return result.rowcount or 0
