from scripts.export_openapi import OUTPUT, build_schema, render


def test_committed_openapi_matches_app() -> None:
    assert OUTPUT.exists(), "run: uv run python -m scripts.export_openapi"
    assert OUTPUT.read_text(encoding="utf-8") == render(build_schema())


def test_schema_documents_health_endpoints() -> None:
    paths = build_schema()["paths"]
    assert "/api/v1/health/live" in paths
    assert "503" in paths["/api/v1/health/ready"]["get"]["responses"]
