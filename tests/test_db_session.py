from typing import Annotated

import pytest
from fastapi import Depends
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.main import create_app
from tests.conftest import SettingsFactory


@pytest.mark.integration
def test_get_db_yields_working_session(
    make_settings: SettingsFactory, test_database_url: str
) -> None:
    app = create_app(make_settings(database_url=test_database_url))

    @app.get("/db-probe")
    def probe(db: Annotated[Session, Depends(get_db)]) -> dict[str, int]:
        return {"value": int(db.execute(text("SELECT 41 + 1")).scalar_one())}

    with TestClient(app, base_url="http://localhost") as client:
        assert client.get("/db-probe").json() == {"value": 42}
