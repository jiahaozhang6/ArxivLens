import os
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

TEST_DATA = Path(__file__).resolve().parents[2] / ".test-data"
TEST_DATA.mkdir(parents=True, exist_ok=True)
TEST_DB = TEST_DATA / "api-test.db"
for suffix in ("", "-wal", "-shm"):
    path = Path(str(TEST_DB) + suffix)
    if path.exists():
        path.unlink()

os.environ["DATABASE_URL"] = f"sqlite+aiosqlite:///{TEST_DB.as_posix()}"
os.environ["SECRET_KEY"] = "test-secret-key"
os.environ["FRONTEND_DIST_DIR"] = str(TEST_DATA / "missing-frontend")
os.environ["DAILY_RUN_LOCK_PATH"] = str(TEST_DATA / ".daily-run.lock")

from app.main import app  # noqa: E402

TEST_USERNAME = "test-admin"
TEST_PASSWORD = "correct horse battery staple"


@pytest.fixture(scope="session")
def client():
    with TestClient(app) as test_client:
        auth_status = test_client.get("/api/auth/status").json()
        if auth_status["setup_required"]:
            response = test_client.post(
                "/api/auth/setup",
                json={"username": TEST_USERNAME, "password": TEST_PASSWORD},
            )
        else:
            response = test_client.post(
                "/api/auth/login",
                json={"username": TEST_USERNAME, "password": TEST_PASSWORD},
            )
        assert response.status_code in {200, 201}, response.text
        csrf_token = test_client.cookies.get("arxivlens_csrf")
        assert csrf_token
        test_client.headers.update({"X-CSRF-Token": csrf_token})
        yield test_client
