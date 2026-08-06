"""Add guardian consent fields

Revision ID: e1a7c9d4b826
Revises: d8b4ef12a7c3
Create Date: 2026-08-06 00:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


revision = "e1a7c9d4b826"
down_revision = "d8b4ef12a7c3"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("travelers") as batch_op:
        batch_op.add_column(sa.Column("is_minor", sa.Boolean(), nullable=True))
        batch_op.add_column(sa.Column("guardian_name", sa.String(length=200), nullable=True))
        batch_op.add_column(sa.Column("guardian_phone", sa.String(length=50), nullable=True))

    with op.batch_alter_table("leads") as batch_op:
        batch_op.add_column(sa.Column("requires_guardian_approval", sa.Boolean(), nullable=True))


def downgrade():
    with op.batch_alter_table("leads") as batch_op:
        batch_op.drop_column("requires_guardian_approval")

    with op.batch_alter_table("travelers") as batch_op:
        batch_op.drop_column("guardian_phone")
        batch_op.drop_column("guardian_name")
        batch_op.drop_column("is_minor")
