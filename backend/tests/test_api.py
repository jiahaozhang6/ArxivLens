import asyncio
from datetime import UTC, datetime

from app.database import SessionLocal
from app.models import Paper, TopicPaper
from app.security import decrypt_secret


async def _seed_paper(topic_id: int, arxiv_id: str = "2609.09999") -> int:
    async with SessionLocal() as session:
        paper = Paper(
            arxiv_id=arxiv_id,
            version=1,
            title="Test-time scaling for scientific reasoning",
            abstract="A controlled study of inference-time compute for scientific question answering.",
            authors=["Ada Example", "Lin Example"],
            categories=["cs.AI"],
            primary_category="cs.AI",
            published_at=datetime(2026, 9, 12, 1, 0, tzinfo=UTC),
            updated_at=datetime(2026, 9, 12, 1, 0, tzinfo=UTC),
            first_seen_at=datetime(2026, 9, 12, 2, 0, tzinfo=UTC),
            last_seen_at=datetime(2026, 9, 12, 2, 0, tzinfo=UTC),
            abs_url=f"https://arxiv.org/abs/{arxiv_id}",
            pdf_url=f"https://arxiv.org/pdf/{arxiv_id}",
        )
        session.add(paper)
        await session.flush()
        session.add(
            TopicPaper(
                topic_id=topic_id,
                paper_id=paper.id,
                discovered_at=datetime(2026, 9, 12, 2, 5, tzinfo=UTC),
            )
        )
        await session.commit()
        return paper.id


async def _seed_library_papers(topic_id: int) -> list[int]:
    records = [
        ("2609.08881", datetime(2026, 9, 11, 2, 0, tzinfo=UTC)),
        ("2609.08882", datetime(2026, 9, 12, 2, 0, tzinfo=UTC)),
    ]
    paper_ids: list[int] = []
    async with SessionLocal() as session:
        for arxiv_id, discovered_at in records:
            paper = Paper(
                arxiv_id=arxiv_id,
                version=1,
                title=f"Library paper {arxiv_id}",
                abstract="Paper used to verify historical browsing and database management.",
                authors=["Library Tester"],
                categories=["cs.AI"],
                primary_category="cs.AI",
                published_at=discovered_at,
                updated_at=discovered_at,
                first_seen_at=discovered_at,
                last_seen_at=discovered_at,
                abs_url=f"https://arxiv.org/abs/{arxiv_id}",
                pdf_url=f"https://arxiv.org/pdf/{arxiv_id}",
            )
            session.add(paper)
            await session.flush()
            session.add(
                TopicPaper(
                    topic_id=topic_id,
                    paper_id=paper.id,
                    discovered_at=discovered_at,
                )
            )
            paper_ids.append(paper.id)
        await session.commit()
    return paper_ids


def test_configuration_and_paper_workflow(client):
    profile_response = client.post(
        "/api/llm-profiles",
        json={
            "name": "DeepSeek Cloud",
            "provider": "deepseek",
            "protocol": "openai_compatible",
            "base_url": "https://api.deepseek.com",
            "model": "deepseek-chat",
            "api_key": "test-api-key",
            "enabled": True,
            "is_default": True,
            "temperature": 0.2,
            "max_tokens": 3000,
            "supports_json_mode": True,
            "extra_body": {},
        },
    )
    assert profile_response.status_code == 201, profile_response.text
    profile = profile_response.json()
    assert profile["has_api_key"] is True
    assert "api_key" not in profile

    topic_response = client.post(
        "/api/topics",
        json={
            "name": "Scientific reasoning",
            "query": 'cat:cs.AI AND all:"scientific reasoning"',
            "description": "Reasoning systems for scientific work",
            "relevance_prompt": "Prioritize reproducible evaluation.",
            "enabled": True,
            "max_results": 8,
            "lookback_days": 4,
            "analyze_pdf": False,
            "llm_profile_id": profile["id"],
        },
    )
    assert topic_response.status_code == 201, topic_response.text
    topic_id = topic_response.json()["id"]
    paper_id = asyncio.run(_seed_paper(topic_id))

    status_response = client.get("/api/system/status")
    assert status_response.status_code == 200
    assert status_response.json()["ready"] is True
    assert status_response.json()["schedule_time"] == "13:00"
    assert status_response.json()["schedule_timezone"] == "Asia/Shanghai"
    assert status_response.json()["schedule_state"] in {
        "pending",
        "starting",
        "running",
        "completed",
        "partial",
        "failed",
        "overdue",
    }

    list_response = client.get("/api/papers?day=2026-09-12&state=unread")
    assert list_response.status_code == 200, list_response.text
    payload = list_response.json()
    assert payload["total"] == 1
    assert payload["items"][0]["id"] == paper_id
    assert payload["stats"]["unread"] == 1

    update_response = client.patch(
        f"/api/papers/{paper_id}",
        json={"is_read": True, "is_starred": True, "decision": "relevant"},
    )
    assert update_response.status_code == 200

    detail_response = client.get(f"/api/papers/{paper_id}")
    assert detail_response.status_code == 200
    detail = detail_response.json()
    assert detail["is_read"] is True
    assert detail["is_starred"] is True
    assert detail["decision"] == "relevant"
    assert detail["topics"][0]["name"] == "Scientific reasoning"


def test_model_discovery_reuses_saved_key_only_for_matching_endpoint(client, monkeypatch):
    profile_response = client.post(
        "/api/llm-profiles",
        json={
            "name": "Model discovery profile",
            "provider": "custom",
            "protocol": "openai_compatible",
            "base_url": "https://api.example.com/v1",
            "model": "model-a",
            "api_key": "saved-secret-key",
            "enabled": True,
            "is_default": False,
            "temperature": 0.2,
            "max_tokens": 3000,
            "supports_json_mode": True,
            "extra_body": {},
        },
    )
    assert profile_response.status_code == 201, profile_response.text
    profile_id = profile_response.json()["id"]
    captured = {}

    async def fake_list_models(protocol, base_url, api_key):
        captured.update(protocol=protocol, base_url=base_url, api_key=api_key)
        return [{"id": "model-a", "label": "Model A"}]

    monkeypatch.setattr("app.routers.profiles.list_available_models", fake_list_models)
    response = client.post(
        "/api/llm-profiles/actions/models",
        json={
            "profile_id": profile_id,
            "protocol": "openai_compatible",
            "base_url": "https://api.example.com/v1/",
        },
    )
    assert response.status_code == 200, response.text
    assert response.json() == {"models": [{"id": "model-a", "label": "Model A"}]}
    assert captured == {
        "protocol": "openai_compatible",
        "base_url": "https://api.example.com/v1/",
        "api_key": "saved-secret-key",
    }

    changed_endpoint_response = client.post(
        "/api/llm-profiles/actions/models",
        json={
            "profile_id": profile_id,
            "protocol": "openai_compatible",
            "base_url": "https://other.example.com/v1",
        },
    )
    assert changed_endpoint_response.status_code == 400
    assert "Re-enter the API key" in changed_endpoint_response.json()["detail"]


def test_email_connection_uses_current_form_without_saving(client, monkeypatch):
    before = client.get("/api/settings").json()["email"]
    captured = {}

    async def fake_send_test_email(config):
        captured.update(
            smtp_host=config.smtp_host,
            smtp_port=config.smtp_port,
            username=config.username,
            password=decrypt_secret(config.encrypted_password, "smtp"),
            from_email=config.from_email,
            recipients=config.recipients,
            security=config.security,
        )

    monkeypatch.setattr("app.routers.settings.send_test_email", fake_send_test_email)
    response = client.post(
        "/api/settings/email/actions/test",
        json={
            "enabled": True,
            "smtp_host": "smtp.example.com",
            "smtp_port": 465,
            "username": "researcher@example.com",
            "password": "smtp-authorization-code",
            "from_email": "researcher@example.com",
            "from_name": "Research Digest",
            "recipients": ["recipient@example.com"],
            "security": "ssl",
            "subject_prefix": "[Test]",
        },
    )

    assert response.status_code == 200, response.text
    assert captured == {
        "smtp_host": "smtp.example.com",
        "smtp_port": 465,
        "username": "researcher@example.com",
        "password": "smtp-authorization-code",
        "from_email": "researcher@example.com",
        "recipients": ["recipient@example.com"],
        "security": "ssl",
    }
    assert client.get("/api/settings").json()["email"] == before


def test_send_today_email_action(client, monkeypatch):
    async def fake_send_today_digest():
        return {"day": "2026-09-12", "papers": 8, "recipients": 1}

    monkeypatch.setattr("app.routers.settings.send_today_digest", fake_send_today_digest)
    response = client.post("/api/settings/email/actions/send-today")

    assert response.status_code == 200
    assert response.json() == {"day": "2026-09-12", "papers": 8, "recipients": 1}


def test_topic_query_assistant_uses_selected_cloud_profile(client, monkeypatch):
    profile_response = client.post(
        "/api/llm-profiles",
        json={
            "name": "Query assistant profile",
            "provider": "custom",
            "protocol": "openai_compatible",
            "base_url": "https://query.example.com/v1",
            "model": "query-model",
            "api_key": "query-secret",
            "enabled": True,
            "is_default": False,
            "temperature": 0.2,
            "max_tokens": 1000,
            "supports_json_mode": True,
            "extra_body": {},
        },
    )
    assert profile_response.status_code == 201
    profile_id = profile_response.json()["id"]
    captured = {}

    async def fake_suggest(profile, research_focus, categories):
        captured.update(
            profile_id=profile.id,
            research_focus=research_focus,
            categories=categories,
        )
        return {
            "name": "大模型推理",
            "query": '(cat:cs.AI OR cat:cs.LG) AND (all:"test-time scaling")',
            "relevance_prompt": "关注测试时计算扩展。",
            "keywords": ["test-time scaling"],
        }

    monkeypatch.setattr("app.routers.topics.suggest_topic_query", fake_suggest)
    response = client.post(
        "/api/topics/actions/suggest-query",
        json={
            "research_focus": "关注大语言模型的测试时计算扩展",
            "categories": ["cs.AI", "cs.LG"],
            "llm_profile_id": profile_id,
        },
    )

    assert response.status_code == 200, response.text
    assert response.json()["name"] == "大模型推理"
    assert captured == {
        "profile_id": profile_id,
        "research_focus": "关注大语言模型的测试时计算扩展",
        "categories": ["cs.AI", "cs.LG"],
    }


def test_historical_dates_and_bulk_paper_management(client, monkeypatch):
    profile_response = client.post(
        "/api/llm-profiles",
        json={
            "name": "Library analysis profile",
            "provider": "custom",
            "protocol": "openai_compatible",
            "base_url": "https://library.example.com/v1",
            "model": "library-model",
            "api_key": "library-secret",
            "enabled": True,
            "is_default": False,
            "temperature": 0.2,
            "max_tokens": 1000,
            "supports_json_mode": True,
            "extra_body": {},
        },
    )
    assert profile_response.status_code == 201
    profile_id = profile_response.json()["id"]
    topic_response = client.post(
        "/api/topics",
        json={
            "name": "Library management topic",
            "query": 'cat:cs.AI AND all:"library management"',
            "description": "Library management test",
            "enabled": True,
            "max_results": 10,
            "lookback_days": 4,
            "analyze_pdf": False,
            "llm_profile_id": profile_id,
        },
    )
    assert topic_response.status_code == 201
    topic_id = topic_response.json()["id"]
    paper_ids = asyncio.run(_seed_library_papers(topic_id))

    dates_response = client.get(f"/api/papers/dates?topic_id={topic_id}")
    assert dates_response.status_code == 200
    assert dates_response.json()["items"] == [
        {"date": "2026-09-12", "count": 1},
        {"date": "2026-09-11", "count": 1},
    ]

    bulk_response = client.post(
        "/api/papers/actions/bulk",
        json={"paper_ids": paper_ids, "action": "star"},
    )
    assert bulk_response.status_code == 200
    assert bulk_response.json()["affected"] == 2
    assert all(client.get(f"/api/papers/{paper_id}").json()["is_starred"] for paper_id in paper_ids)

    created = []

    async def fake_create_analysis(paper_id, topic_id, profile_id, source_mode, language):
        created.append((paper_id, topic_id, profile_id, source_mode, language))
        return 9000 + len(created)

    async def fake_execute_batch(_analysis_ids):
        return None

    monkeypatch.setattr("app.routers.papers.create_analysis", fake_create_analysis)
    monkeypatch.setattr("app.routers.papers._execute_analysis_batch", fake_execute_batch)
    analyze_response = client.post(
        "/api/papers/actions/analyze",
        json={"paper_ids": paper_ids},
    )
    assert analyze_response.status_code == 202, analyze_response.text
    assert analyze_response.json()["submitted"] == 2
    assert {record[0] for record in created} == set(paper_ids)

    delete_response = client.post(
        "/api/papers/actions/bulk",
        json={"paper_ids": [paper_ids[1]], "action": "delete"},
    )
    assert delete_response.status_code == 200
    assert client.get(f"/api/papers/{paper_ids[1]}").status_code == 404


def test_paper_chat_streams_and_persists_messages(client, monkeypatch):
    profile_response = client.post(
        "/api/llm-profiles",
        json={
            "name": "Paper chat profile",
            "provider": "custom",
            "protocol": "openai_compatible",
            "base_url": "https://chat.example.com/v1",
            "model": "chat-model",
            "api_key": "chat-secret",
            "enabled": True,
            "is_default": False,
            "temperature": 0.2,
            "max_tokens": 1200,
            "supports_json_mode": True,
            "extra_body": {},
        },
    )
    profile_id = profile_response.json()["id"]
    topic_response = client.post(
        "/api/topics",
        json={
            "name": "Paper chat topic",
            "query": 'cat:cs.AI AND all:"paper chat"',
            "enabled": True,
            "max_results": 5,
            "lookback_days": 4,
            "analyze_pdf": False,
            "llm_profile_id": profile_id,
        },
    )
    paper_id = asyncio.run(_seed_paper(topic_response.json()["id"], "2609.07777"))
    session_response = client.post(
        f"/api/papers/{paper_id}/chat/sessions",
        json={"llm_profile_id": profile_id},
    )
    assert session_response.status_code == 201, session_response.text
    chat_session_id = session_response.json()["id"]

    async def fake_stream(_session, preferred_profile_id, _operation):
        assert preferred_profile_id == profile_id
        profile = {
            "profile_id": profile_id,
            "name": "Paper chat profile",
            "provider": "custom",
            "model": "chat-model",
        }
        yield {"type": "model", "profile": profile, "fallback_used": False}
        yield {"type": "delta", "text": "核心创新是"}
        yield {"type": "delta", "text": "测试时计算扩展。"}
        yield {
            "type": "done",
            "routing": {
                "preferred_profile_id": profile_id,
                "used": profile,
                "fallback_used": False,
                "attempts": [{**profile, "status": "completed", "error": ""}],
            },
            "warning": None,
        }

    monkeypatch.setattr("app.routers.chats.stream_with_profile_fallback", fake_stream)
    with client.stream(
        "POST",
        f"/api/papers/{paper_id}/chat/sessions/{chat_session_id}/messages/stream",
        json={"content": "这篇论文的核心创新是什么？", "llm_profile_id": profile_id},
    ) as response:
        stream_text = response.read().decode()

    assert response.status_code == 200
    assert "event: delta" in stream_text
    assert "测试时计算扩展" in stream_text
    detail = client.get(
        f"/api/papers/{paper_id}/chat/sessions/{chat_session_id}"
    ).json()
    assert detail["title"].startswith("这篇论文")
    assert [message["role"] for message in detail["messages"]] == ["user", "assistant"]
    assert detail["messages"][1]["status"] == "completed"
    assert detail["messages"][1]["content"] == "核心创新是测试时计算扩展。"
    assert detail["messages"][1]["model_routing"]["fallback_used"] is False
