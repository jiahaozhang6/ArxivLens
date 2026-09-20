import asyncio
import base64
import hashlib
import secrets

from fastapi.testclient import TestClient
from sqlalchemy import delete

from app.auth import (
    PBKDF2_ITERATIONS,
    hash_password,
    password_needs_rehash,
    validate_password_strength,
    verify_password,
)
from app.database import SessionLocal
from app.main import app
from app.models import Paper, PaperDecision, utcnow
from tests.conftest import TEST_PASSWORD, TEST_USERNAME


def _set_csrf(client: TestClient) -> None:
    token = client.cookies.get("arxivlens_csrf")
    assert token
    client.headers.update({"X-CSRF-Token": token})


def test_password_hash_is_salted_and_verifiable():
    first = hash_password(TEST_PASSWORD)
    second = hash_password(TEST_PASSWORD)
    assert first != second
    assert verify_password(TEST_PASSWORD, first)
    assert not verify_password("wrong-password-value", first)
    assert not password_needs_rehash(first)


def test_legacy_password_hash_remains_valid_and_requires_upgrade():
    salt = secrets.token_bytes(24)
    digest = hashlib.pbkdf2_hmac(
        "sha256", TEST_PASSWORD.encode("utf-8"), salt, PBKDF2_ITERATIONS
    )
    encoded = "$".join(
        (
            "pbkdf2_sha256",
            str(PBKDF2_ITERATIONS),
            base64.urlsafe_b64encode(salt).decode("ascii").rstrip("="),
            base64.urlsafe_b64encode(digest).decode("ascii").rstrip("="),
        )
    )
    assert verify_password(TEST_PASSWORD, encoded)
    assert password_needs_rehash(encoded)


def test_password_strength_rejects_weak_or_username_based_values():
    for password in ("short", "password123456", "test-admin-Strong-2026"):
        try:
            validate_password_strength(password, TEST_USERNAME)
        except ValueError:
            continue
        raise AssertionError(f"Weak password was accepted: {password}")
    validate_password_strength(TEST_PASSWORD, TEST_USERNAME)


def test_authentication_and_csrf_protect_api(client):
    with TestClient(app) as anonymous:
        assert anonymous.get("/api/papers").status_code == 401
        status_response = anonymous.get("/api/auth/status")
        assert status_response.status_code == 200
        assert status_response.json() == {
            "setup_required": False,
            "authenticated": False,
            "role": None,
            "user": None,
            "session_expires_at": None,
        }
        assert anonymous.post(
            "/api/auth/login",
            json={"username": TEST_USERNAME, "password": "incorrect-password-value"},
        ).status_code == 401
        login = anonymous.post(
            "/api/auth/login",
            json={"username": TEST_USERNAME, "password": TEST_PASSWORD},
        )
        assert login.status_code == 200, login.text
        assert login.json()["authenticated"] is True
        assert login.json()["role"] == "admin"
        assert anonymous.post("/api/auth/logout").status_code == 403
        _set_csrf(anonymous)
        assert anonymous.post("/api/auth/logout").status_code == 204
        assert anonymous.get("/api/papers").status_code == 401

    _set_csrf(client)


async def _seed_private_paper() -> int:
    now = utcnow()
    async with SessionLocal() as session:
        paper = Paper(
            arxiv_id=f"guest-private-{now.timestamp()}",
            title="Guest-visible paper",
            abstract="Public abstract for guest access testing.",
            authors=["Test Author"],
            categories=["cs.AI"],
            primary_category="cs.AI",
            published_at=now,
            updated_at=now,
            abs_url="https://arxiv.org/abs/guest-private",
            pdf_url="https://arxiv.org/pdf/guest-private",
            is_read=True,
            is_starred=True,
            decision=PaperDecision.relevant,
            personal_notes="private research note",
            user_tags=["private-tag"],
        )
        session.add(paper)
        await session.commit()
        await session.refresh(paper)
        return paper.id


async def _delete_paper(paper_id: int) -> None:
    async with SessionLocal() as session:
        await session.execute(delete(Paper).where(Paper.id == paper_id))
        await session.commit()


def test_guest_session_is_read_only_and_hides_private_fields(client):
    paper_id = asyncio.run(_seed_private_paper())
    try:
        with TestClient(app) as guest:
            login = guest.post("/api/auth/guest")
            assert login.status_code == 200, login.text
            assert login.json()["role"] == "guest"
            assert login.json()["user"] is None

            status_response = guest.get("/api/auth/status")
            assert status_response.status_code == 200
            assert status_response.json()["role"] == "guest"

            assert guest.get("/api/papers").status_code == 200
            assert guest.get("/api/papers/dates").status_code == 200
            assert guest.get("/api/topics").status_code == 200
            assert guest.get("/api/system/status").status_code == 200
            detail = guest.get(f"/api/papers/{paper_id}")
            assert detail.status_code == 200, detail.text
            payload = detail.json()
            assert payload["is_read"] is False
            assert payload["is_starred"] is False
            assert payload["decision"] == "unreviewed"
            assert payload["personal_notes"] is None
            assert payload["user_tags"] == []

            assert guest.patch(f"/api/papers/{paper_id}", json={"is_read": False}).status_code == 403
            assert guest.get(f"/api/papers/{paper_id}/export/markdown").status_code == 403
            assert guest.get("/api/llm-profiles").status_code == 403
            assert guest.get("/api/settings").status_code == 403
            assert guest.post("/api/jobs/runs", json={"topic_ids": None}).status_code == 403

            assert guest.post("/api/auth/logout").status_code == 204
            assert guest.get("/api/papers").status_code == 401
    finally:
        asyncio.run(_delete_paper(paper_id))

    _set_csrf(client)


def test_password_change_rotates_sessions_and_can_be_restored(client):
    replacement = "replacement password for tests"
    response = client.post(
        "/api/auth/change-password",
        json={"current_password": TEST_PASSWORD, "new_password": replacement},
    )
    assert response.status_code == 200, response.text
    _set_csrf(client)
    assert client.post(
        "/api/auth/login",
        json={"username": TEST_USERNAME, "password": TEST_PASSWORD},
    ).status_code == 401
    assert client.post(
        "/api/auth/change-password",
        json={"current_password": replacement, "new_password": TEST_PASSWORD},
    ).status_code == 200
    _set_csrf(client)
