"""Add employee follow-up fields

Revision ID: b52e9d6a31f4
Revises: 9c2a7f1d4b6c
Create Date: 2026-07-16 13:45:00.000000

"""
from alembic import op
import sqlalchemy as sa


revision = "b52e9d6a31f4"
down_revision = "9c2a7f1d4b6c"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("leads") as batch_op:
        batch_op.add_column(sa.Column("assigned_to", sa.String(length=100), nullable=True))
        batch_op.add_column(sa.Column("last_contact_at", sa.DateTime(), nullable=True))
        batch_op.add_column(sa.Column("customer_response_status", sa.String(length=100), nullable=True))

    with op.batch_alter_table("trip_bookings") as batch_op:
        batch_op.add_column(sa.Column("assigned_to", sa.String(length=100), nullable=True))
        batch_op.add_column(sa.Column("priority", sa.String(length=50), nullable=True))
        batch_op.add_column(sa.Column("next_follow_up_at", sa.DateTime(), nullable=True))
        batch_op.add_column(sa.Column("next_action", sa.String(length=200), nullable=True))
        batch_op.add_column(sa.Column("last_contact_at", sa.DateTime(), nullable=True))
        batch_op.add_column(sa.Column("customer_response_status", sa.String(length=100), nullable=True))


def downgrade():
    with op.batch_alter_table("trip_bookings") as batch_op:
        batch_op.drop_column("customer_response_status")
        batch_op.drop_column("last_contact_at")
        batch_op.drop_column("next_action")
        batch_op.drop_column("next_follow_up_at")
        batch_op.drop_column("priority")
        batch_op.drop_column("assigned_to")

    with op.batch_alter_table("leads") as batch_op:
        batch_op.drop_column("customer_response_status")
        batch_op.drop_column("last_contact_at")
        batch_op.drop_column("assigned_to")
