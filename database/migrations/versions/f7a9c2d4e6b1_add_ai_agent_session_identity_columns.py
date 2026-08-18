"""add_ai_agent_session_identity_columns

Revision ID: f7a9c2d4e6b1
Revises: e5a1d73c8b46
Create Date: 2026-08-19 00:00:00.000001

"""
from alembic import op
import sqlalchemy as sa


revision = "f7a9c2d4e6b1"
down_revision = "e5a1d73c8b46"
branch_labels = None
depends_on = None


IDENTITY_COLUMNS = {
    "traveler_id": sa.String(length=20),
    "lead_id": sa.String(length=50),
    "raw_phone": sa.String(length=32),
}


def _table_exists(table_name: str) -> bool:
    return table_name in sa.inspect(op.get_bind()).get_table_names()


def _column_exists(table_name: str, column_name: str) -> bool:
    return any(column["name"] == column_name for column in sa.inspect(op.get_bind()).get_columns(table_name))


def _index_exists(table_name: str, index_name: str) -> bool:
    return any(index["name"] == index_name for index in sa.inspect(op.get_bind()).get_indexes(table_name))


def upgrade():
    if not _table_exists("ai_agent_sessions"):
        op.create_table(
            "ai_agent_sessions",
            sa.Column("session_id", sa.String(length=64), nullable=False),
            sa.Column("schema_version", sa.Integer(), nullable=False, server_default="1"),
            sa.Column("payload", sa.Text(), nullable=False),
            sa.Column("agent_state", sa.Text(), nullable=True),
            sa.Column("version", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("locked_until", sa.DateTime(timezone=True), nullable=True),
            sa.Column("lock_owner", sa.String(length=80), nullable=True),
            sa.Column("last_message_key", sa.String(length=160), nullable=True),
            sa.Column("traveler_id", sa.String(length=20), nullable=True),
            sa.Column("lead_id", sa.String(length=50), nullable=True),
            sa.Column("raw_phone", sa.String(length=32), nullable=True),
            sa.PrimaryKeyConstraint("session_id"),
        )
    else:
        for column_name, column_type in IDENTITY_COLUMNS.items():
            if not _column_exists("ai_agent_sessions", column_name):
                op.add_column("ai_agent_sessions", sa.Column(column_name, column_type, nullable=True))

    for index_name, columns in (
        ("ix_ai_agent_sessions_locked_until", ["locked_until"]),
        ("ix_ai_agent_sessions_traveler_id", ["traveler_id"]),
        ("ix_ai_agent_sessions_lead_id", ["lead_id"]),
    ):
        if not _index_exists("ai_agent_sessions", index_name):
            op.create_index(index_name, "ai_agent_sessions", columns)


def downgrade():
    for index_name in (
        "ix_ai_agent_sessions_lead_id",
        "ix_ai_agent_sessions_traveler_id",
    ):
        if _table_exists("ai_agent_sessions") and _index_exists("ai_agent_sessions", index_name):
            op.drop_index(index_name, table_name="ai_agent_sessions")
