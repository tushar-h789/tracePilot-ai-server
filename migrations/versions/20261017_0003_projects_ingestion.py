"""Phase 3: projects, ingestion keys and log events.

log_events is idempotent per project (UNIQUE(project_id, event_id)) and carries a generated
`message_tsv` column + GIN index for the Phase 4 full-text search.

Revision ID: 0003_projects_ingestion
Revises: 0002_identity_access
Create Date: 2026-10-17 07:15:35.992261+00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0003_projects_ingestion"
down_revision: str | None = "0002_identity_access"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "projects",
        sa.Column("id", sa.Uuid(), server_default=sa.text("uuidv7()"), nullable=False),
        sa.Column("org_id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.String(length=100), nullable=False),
        sa.Column("slug", sa.String(length=48), nullable=False),
        sa.Column(
            "environment",
            sa.String(length=32),
            server_default=sa.text("'production'"),
            nullable=False,
        ),
        sa.Column("retention_days", sa.Integer(), server_default=sa.text("7"), nullable=False),
        sa.Column("created_by", sa.Uuid(), nullable=True),
        sa.Column("archived_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "environment ~ '^[a-z0-9][a-z0-9_-]{0,31}$'",
            name=op.f("ck_projects_environment_format"),
        ),
        sa.CheckConstraint(
            "slug ~ '^[a-z0-9][a-z0-9-]{1,46}[a-z0-9]$'", name=op.f("ck_projects_slug_format")
        ),
        sa.CheckConstraint(
            "char_length(btrim(name)) BETWEEN 1 AND 100", name=op.f("ck_projects_name_length")
        ),
        sa.CheckConstraint(
            "retention_days BETWEEN 1 AND 30", name=op.f("ck_projects_retention_days_range")
        ),
        sa.ForeignKeyConstraint(
            ["created_by"],
            ["users.id"],
            name=op.f("fk_projects_created_by_users"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["org_id"],
            ["organizations.id"],
            name=op.f("fk_projects_org_id_organizations"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_projects")),
        sa.UniqueConstraint("org_id", "slug", name="uq_projects_org_id_slug"),
    )
    op.create_table(
        "ingestion_keys",
        sa.Column("id", sa.Uuid(), server_default=sa.text("uuidv7()"), nullable=False),
        sa.Column("project_id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.String(length=100), nullable=False),
        sa.Column("prefix", sa.String(length=32), nullable=False),
        sa.Column("key_hash", sa.String(length=64), nullable=False),
        sa.Column("created_by", sa.Uuid(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("replaced_by_id", sa.Uuid(), nullable=True),
        sa.CheckConstraint(
            "char_length(btrim(name)) BETWEEN 1 AND 100", name=op.f("ck_ingestion_keys_name_length")
        ),
        sa.CheckConstraint(
            "char_length(key_hash) = 64", name=op.f("ck_ingestion_keys_key_hash_length")
        ),
        sa.ForeignKeyConstraint(
            ["created_by"],
            ["users.id"],
            name=op.f("fk_ingestion_keys_created_by_users"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["project_id"],
            ["projects.id"],
            name=op.f("fk_ingestion_keys_project_id_projects"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["replaced_by_id"],
            ["ingestion_keys.id"],
            name=op.f("fk_ingestion_keys_replaced_by_id_ingestion_keys"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_ingestion_keys")),
        sa.UniqueConstraint("key_hash", name=op.f("uq_ingestion_keys_key_hash")),
        sa.UniqueConstraint("prefix", name=op.f("uq_ingestion_keys_prefix")),
    )
    op.create_index(
        "ix_ingestion_keys_project_id_active",
        "ingestion_keys",
        ["project_id"],
        unique=False,
        postgresql_where=sa.text("revoked_at IS NULL"),
    )
    op.create_table(
        "log_events",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=True), nullable=False),
        sa.Column("project_id", sa.Uuid(), nullable=False),
        sa.Column("ingestion_key_id", sa.Uuid(), nullable=True),
        sa.Column("event_id", sa.Uuid(), nullable=False),
        sa.Column("ts", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "received_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("level", sa.String(length=16), nullable=False),
        sa.Column("service", sa.String(length=100), nullable=False),
        sa.Column("environment", sa.String(length=32), nullable=False),
        sa.Column("message", sa.Text(), nullable=False),
        sa.Column("trace_id", sa.String(length=64), nullable=True),
        sa.Column("request_id", sa.String(length=128), nullable=True),
        sa.Column("http_method", sa.String(length=10), nullable=True),
        sa.Column("http_path", sa.String(length=512), nullable=True),
        sa.Column("http_status", sa.SmallInteger(), nullable=True),
        sa.Column("duration_ms", sa.Float(), nullable=True),
        sa.Column(
            "attributes",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column("is_synthetic", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column(
            "message_tsv",
            postgresql.TSVECTOR(),
            sa.Computed("to_tsvector('simple', message)", persisted=True),
            nullable=False,
        ),
        sa.CheckConstraint(
            "level IN ('debug', 'info', 'warning', 'error', 'critical')",
            name=op.f("ck_log_events_level_valid"),
        ),
        sa.CheckConstraint(
            "char_length(message) <= 65536", name=op.f("ck_log_events_message_length")
        ),
        sa.CheckConstraint(
            "duration_ms IS NULL OR duration_ms >= 0",
            name=op.f("ck_log_events_duration_non_negative"),
        ),
        sa.CheckConstraint(
            "http_status IS NULL OR http_status BETWEEN 100 AND 599",
            name=op.f("ck_log_events_http_status_range"),
        ),
        sa.ForeignKeyConstraint(
            ["ingestion_key_id"],
            ["ingestion_keys.id"],
            name=op.f("fk_log_events_ingestion_key_id_ingestion_keys"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["project_id"],
            ["projects.id"],
            name=op.f("fk_log_events_project_id_projects"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_log_events")),
        sa.UniqueConstraint("project_id", "event_id", name="uq_log_events_project_id_event_id"),
    )
    op.create_index(
        "ix_log_events_message_tsv",
        "log_events",
        ["message_tsv"],
        unique=False,
        postgresql_using="gin",
    )
    op.create_index(
        "ix_log_events_project_id_level_ts",
        "log_events",
        ["project_id", "level", sa.literal_column("ts DESC")],
        unique=False,
    )
    op.create_index(
        "ix_log_events_project_id_service_ts",
        "log_events",
        ["project_id", "service", sa.literal_column("ts DESC")],
        unique=False,
    )
    op.create_index(
        "ix_log_events_project_id_trace_id",
        "log_events",
        ["project_id", "trace_id"],
        unique=False,
        postgresql_where=sa.text("trace_id IS NOT NULL"),
    )
    op.create_index(
        "ix_log_events_project_id_ts",
        "log_events",
        ["project_id", sa.literal_column("ts DESC")],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_log_events_project_id_ts", table_name="log_events")
    op.drop_index(
        "ix_log_events_project_id_trace_id",
        table_name="log_events",
        postgresql_where=sa.text("trace_id IS NOT NULL"),
    )
    op.drop_index("ix_log_events_project_id_service_ts", table_name="log_events")
    op.drop_index("ix_log_events_project_id_level_ts", table_name="log_events")
    op.drop_index("ix_log_events_message_tsv", table_name="log_events", postgresql_using="gin")
    op.drop_table("log_events")
    op.drop_index(
        "ix_ingestion_keys_project_id_active",
        table_name="ingestion_keys",
        postgresql_where=sa.text("revoked_at IS NULL"),
    )
    op.drop_table("ingestion_keys")
    op.drop_table("projects")
