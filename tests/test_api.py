"""End-to-end API tests using FastAPI's TestClient with SQLite + fakeredis-style skip for queue."""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

import io
import pytest
from fastapi.testclient import TestClient

os.environ["DATABASE_URL"] = "sqlite:///./test_e2e.db"
os.environ["DESKTOP_API_KEY"] = "test-key"

from app.main import app  # noqa: E402

client = TestClient(app)
HEADERS = {"X-API-Key": "test-key"}


def test_health_no_auth_required():
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"


def test_upload_requires_api_key():
    resp = client.post("/upload", files={"file": ("a.mp3", io.BytesIO(b"fake"), "audio/mpeg")})
    assert resp.status_code == 401


def test_upload_rejects_unsupported_extension():
    resp = client.post(
        "/upload", headers=HEADERS, files={"file": ("a.pdf", io.BytesIO(b"fake"), "application/pdf")}
    )
    assert resp.status_code == 400


def test_job_not_found_returns_404():
    resp = client.get("/jobs/does-not-exist", headers=HEADERS)
    assert resp.status_code == 404
