"""Low-level dependency probes. Each raises on failure; callers decide how to report it."""

from __future__ import annotations

from redis import Redis
from sqlalchemy import Engine, text


def ping_database(engine: Engine) -> None:
    with engine.connect() as conn:
        conn.execute(text("SELECT 1")).scalar_one()


def ping_redis(client: Redis) -> None:
    if client.ping() is not True:
        raise RuntimeError("unexpected PING reply")
