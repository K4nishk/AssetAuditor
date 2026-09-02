"""AA-4 smoke test: the hello-world API is live and reachable at the exact
path `vercel.json`'s `/api/:path*` rewrite forwards to it.
"""

from fastapi.testclient import TestClient

from app.main import app


def test_health_endpoint_returns_ok():
    client = TestClient(app)
    response = client.get("/api/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok", "service": "AssetAuditor"}


def test_health_endpoint_is_not_reachable_without_the_api_prefix():
    # Only "/api/health" is the public contract — vercel.json never forwards
    # anything outside /api/* to this function.
    client = TestClient(app)
    assert client.get("/health").status_code == 404
