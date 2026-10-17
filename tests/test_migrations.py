from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy import create_engine, pool

ROOT = Path(__file__).resolve().parent.parent


def _config(database_url: str | None = None) -> Config:
    cfg = Config(str(ROOT / "alembic.ini"))
    cfg.attributes["skip_logging_config"] = True
    if database_url:
        cfg.attributes["database_url"] = database_url
    return cfg


def _current(database_url: str) -> str | None:
    engine = create_engine(database_url, poolclass=pool.NullPool)
    try:
        with engine.connect() as conn:
            return MigrationContext.configure(conn).get_current_revision()
    finally:
        engine.dispose()


def test_single_head_and_linear_history() -> None:
    script = ScriptDirectory.from_config(_config())
    assert len(script.get_heads()) == 1
    assert script.get_base() == "0001_baseline"


@pytest.mark.integration
def test_upgrade_downgrade_roundtrip(test_database_url: str) -> None:
    cfg = _config(test_database_url)
    head = ScriptDirectory.from_config(cfg).get_current_head()

    command.upgrade(cfg, "head")
    assert _current(test_database_url) == head
    command.downgrade(cfg, "base")
    assert _current(test_database_url) is None
    command.upgrade(cfg, "head")
    assert _current(test_database_url) == head
