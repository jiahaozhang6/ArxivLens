from fastapi.testclient import TestClient

from app.auth import hash_password, verify_password
from app.main import app
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


def test_authentication_and_csrf_protect_api(client):
    with TestClient(app) as anonymous:
        assert anonymous.get("/api/papers").status_code == 401
        status_response = anonymous.get("/api/auth/status")
        assert status_response.status_code == 200
        assert status_response.json() == {
            "setup_required": False,
            "authenticated": False,
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
        assert anonymous.post("/api/auth/logout").status_code == 403
        _set_csrf(anonymous)
        assert anonymous.post("/api/auth/logout").status_code == 204
        assert anonymous.get("/api/papers").status_code == 401

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
