"""AA-1 smoke test: the FastAPI app is importable and wired end-to-end."""

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.main import app


def test_app_is_a_fastapi_instance():
    assert isinstance(app, FastAPI)
    assert app.title == "AssetAuditor"


def test_app_serves_requests():
    client = TestClient(app)
    response = client.get("/")
    # No routes are registered yet (each ships with its own issue) — a clean
    # 404 proves the ASGI app is mounted and responding, not crashing.
    assert response.status_code == 404
