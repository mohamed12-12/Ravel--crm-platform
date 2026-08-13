"""add_interaction_message_key_unique_index

Revision ID: e7b2c4d9a1f0
Revises: f2b6c8d9e4a1
Create Date: 2026-08-13 00:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


revision = "e7b2c4d9a1f0"
down_revision = "f2b6c8d9e4a1"
branch_labels = None
depends_on = None


INDEX_NAME = "ux_interactions_channel_message_key"


def _table_exists(table_name: str) -> bool:
    return table_name in sa.inspect(op.get_bind()).get_table_names()


def _index_exists(table_name: str, index_name: str) -> bool:
    return any(index["name"] == index_name for index in sa.inspect(op.get_bind()).get_indexes(table_name))


def _assert_no_duplicate_message_keys() -> None:
    bind = op.get_bind()
    duplicate = bind.execute(
        sa.text(
            """
            SELECT channel, message_key, COUNT(*) AS duplicate_count
            FROM interactions
            WHERE message_key IS NOT NULL
              AND message_key <> ''
            GROUP BY channel, message_key
            HAVING COUNT(*) > 1
            LIMIT 1
            """
        )
    ).mappings().first()
    if duplicate:
        raise RuntimeError(
            "Cannot add unique webhook message index: duplicate interaction "
            f"exists for channel={duplicate['channel']!r}, message_key={duplicate['message_key']!r}."
        )


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
            sa.PrimaryKeyConstraint("session_id"),
        )
    if not _index_exists("ai_agent_sessions", "ix_ai_agent_sessions_locked_until"):
        op.create_index("ix_ai_agent_sessions_locked_until", "ai_agent_sessions", ["locked_until"])
    _assert_no_duplicate_message_keys()
    if not _index_exists("interactions", INDEX_NAME):
        op.create_index(
            INDEX_NAME,
            "interactions",
            ["channel", "message_key"],
            unique=True,
            postgresql_where=sa.text("message_key IS NOT NULL AND message_key <> ''"),
            sqlite_where=sa.text("message_key IS NOT NULL AND message_key <> ''"),
        )


def downgrade():
    op.drop_index(INDEX_NAME, table_name="interactions")
    op.drop_index("ix_ai_agent_sessions_locked_until", table_name="ai_agent_sessions")
    op.drop_table("ai_agent_sessions")
