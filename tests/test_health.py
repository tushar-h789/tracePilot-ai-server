import pytest
from fastapi.testclient import TestClient

from app.core.security import REQUEST_ID_HEADER


def test_live_returns_200_without_dependencies(client: TestClient) -> None:
    response = client.get("/api/v1/health/live")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_live_sets_security_headers_and_request_id(client: TestClient) -> None:
    response = client.get("/api/v1/health/live")
    assert response.headers["X-Content-Type-Options"] == "nosniff"
    assert response.headers["X-Frame-Options"] == "DENY"
    assert "frame-ancestors 'none'" in response.headers["Content-Security-Policy"]
    assert "Strict-Transport-Security" not in response.headers  # only in prod
    assert len(response.headers[REQUEST_ID_HEADER]) >= 8


def test_valid_request_id_is_propagated(client: TestClient) -> None:
    response = client.get("/api/v1/health/live", headers={REQUEST_ID_HEADER: "abc-12345678"})
    assert response.headers[REQUEST_ID_HEADER] == "abc-12345678"


def test_malformed_request_id_is_replaced(client: TestClient) -> None:
    response = client.get("/api/v1/health/live", headers={REQUEST_ID_HEADER: "bad id\twith<chars>"})
    assert response.headers[REQUEST_ID_HEADER] != "bad id\twith<chars>"


def test_ready_returns_503_when_dependencies_unreachable(client: TestClient) -> None:
    response = client.get("/api/v1/health/ready")
    assert response.status_code == 503
    body = response.json()
    assert body["status"] == "unavailable"
    assert body["checks"]["database"]["status"] == "error"
    assert body["checks"]["redis"]["status"] == "error"
    assert response.headers["Cache-Control"] == "no-store"
    text = response.text.lower()
    for leak in ("traceback", "operationalerror", "connection refused", "127.0.0.1", "unit-test"):
        assert leak not in text


@pytest.mark.integration
def test_ready_returns_200_with_real_postgres_and_redis(integration_client: TestClient) -> None:
    response = integration_client.get("/api/v1/health/ready")
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["status"] == "ok"
    assert set(body["checks"]) == {"database", "redis"}
    assert all(check["status"] == "ok" for check in body["checks"].values())
    assert all(check["latency_ms"] >= 0 for check in body["checks"].values())


@pytest.mark.integration
def test_ready_returns_503_when_only_redis_is_down(
    make_settings: object, test_database_url: str
) -> None:
    from app.main import create_app
    from tests.conftest import UNREACHABLE_REDIS

    settings = make_settings(database_url=test_database_url, redis_url=UNREACHABLE_REDIS)  # type: ignore[operator]
    with TestClient(create_app(settings), base_url="http://localhost") as client:
        response = client.get("/api/v1/health/ready")
    assert response.status_code == 503
    assert response.json()["checks"]["database"]["status"] == "ok"
    assert response.json()["checks"]["redis"]["status"] == "error"
