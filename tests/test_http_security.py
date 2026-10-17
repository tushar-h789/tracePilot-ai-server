from fastapi.testclient import TestClient

from app.main import create_app
from tests.conftest import SettingsFactory


def test_untrusted_host_is_rejected(client: TestClient) -> None:
    response = client.get("/api/v1/health/live", headers={"Host": "evil.example"})
    assert response.status_code == 400


def test_cors_allows_configured_origin_only(client: TestClient) -> None:
    preflight = {"Access-Control-Request-Method": "GET"}
    allowed = client.options(
        "/api/v1/health/live", headers={"Origin": "http://localhost:3100", **preflight}
    )
    assert allowed.status_code == 200
    assert allowed.headers["access-control-allow-origin"] == "http://localhost:3100"

    denied = client.options(
        "/api/v1/health/live", headers={"Origin": "https://evil.example", **preflight}
    )
    assert denied.status_code == 400
    assert "access-control-allow-origin" not in denied.headers


def test_unhandled_errors_hide_internals(make_settings: SettingsFactory) -> None:
    app = create_app(
        make_settings(
            app_env="prod",
            allowed_hosts="localhost",
            cookie_secure=True,
            argon2_memory_cost_kib=19456,
        )
    )

    @app.get("/boom")
    def boom() -> None:
        raise RuntimeError("database password is hunter2")

    with TestClient(app, base_url="http://localhost", raise_server_exceptions=False) as client:
        response = client.get("/boom")
    assert response.status_code == 500
    assert response.headers["content-type"].startswith("application/problem+json")
    body = response.json()
    assert body["title"] == "Internal Server Error"
    assert body["request_id"]
    assert "hunter2" not in response.text
    assert "RuntimeError" not in response.text
    assert response.headers["Strict-Transport-Security"].startswith("max-age=")


def test_docs_and_openapi_disabled_in_prod(make_settings: SettingsFactory) -> None:
    app = create_app(
        make_settings(
            app_env="prod",
            allowed_hosts="localhost",
            cookie_secure=True,
            argon2_memory_cost_kib=19456,
        )
    )
    with TestClient(app, base_url="http://localhost") as client:
        assert client.get("/api/v1/openapi.json").status_code == 404
        assert client.get("/api/v1/docs").status_code == 404


def test_openapi_served_in_dev(client: TestClient) -> None:
    response = client.get("/api/v1/openapi.json")
    assert response.status_code == 200
    assert "/api/v1/health/ready" in response.json()["paths"]
