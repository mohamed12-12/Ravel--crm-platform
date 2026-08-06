"""add trip media

Revision ID: d8b4ef12a7c3
Revises: c61e4a2f9b10
Create Date: 2026-07-18 00:00:00.000000
"""

from alembic import op
import sqlalchemy as sa


revision = "d8b4ef12a7c3"
down_revision = "c61e4a2f9b10"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "trip_media",
        sa.Column("media_id", sa.Integer(), primary_key=True),
        sa.Column("public_id", sa.String(length=36), nullable=False, unique=True),
        sa.Column("trip_id", sa.String(length=50), nullable=False),
        sa.Column("storage_key", sa.String(length=500), nullable=False, unique=True),
        sa.Column("public_url", sa.String(length=500), nullable=False),
        sa.Column("image_type", sa.String(length=20), nullable=False, server_default="gallery"),
        sa.Column("alt_text", sa.String(length=255), nullable=True),
        sa.Column("display_order", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("original_filename", sa.String(length=255), nullable=True),
        sa.Column("mime_type", sa.String(length=100), nullable=False),
        sa.Column("file_extension", sa.String(length=10), nullable=False),
        sa.Column("file_size", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("uploaded_by_user_id", sa.Integer(), nullable=True),
        sa.Column("uploaded_by_name", sa.String(length=100), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("verification_status", sa.String(length=50), nullable=False, server_default="verified"),
        sa.ForeignKeyConstraint(["trip_id"], ["trips.trip_id"]),
        sa.ForeignKeyConstraint(["uploaded_by_user_id"], ["users.id"]),
    )
    op.create_index("ix_trip_media_trip_id", "trip_media", ["trip_id"])
    op.create_index("ix_trip_media_public_id", "trip_media", ["public_id"])
    op.create_index("ix_trip_media_image_type", "trip_media", ["image_type"])
    op.create_index("ix_trip_media_is_active", "trip_media", ["is_active"])
    op.create_index("ix_trip_media_verification_status", "trip_media", ["verification_status"])


def downgrade():
    op.drop_index("ix_trip_media_verification_status", table_name="trip_media")
    op.drop_index("ix_trip_media_is_active", table_name="trip_media")
    op.drop_index("ix_trip_media_image_type", table_name="trip_media")
    op.drop_index("ix_trip_media_public_id", table_name="trip_media")
    op.drop_index("ix_trip_media_trip_id", table_name="trip_media")
    op.drop_table("trip_media")
