"""Add booking event trail

Revision ID: 9c2a7f1d4b6c
Revises: 7835d45ae61b
Create Date: 2026-05-18 13:35:00.000000

"""
from alembic import op
import sqlalchemy as sa


revision = '9c2a7f1d4b6c'
down_revision = '7835d45ae61b'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        'booking_event_trail',
        sa.Column('event_id', sa.String(length=50), nullable=False),
        sa.Column('occurred_at', sa.DateTime(), nullable=True),
        sa.Column('event_type', sa.String(length=100), nullable=True),
        sa.Column('event_label', sa.String(length=150), nullable=True),
        sa.Column('traveler_id', sa.String(length=20), nullable=True),
        sa.Column('lead_id', sa.String(length=50), nullable=True),
        sa.Column('booking_id', sa.String(length=50), nullable=True),
        sa.Column('trip_id', sa.String(length=50), nullable=True),
        sa.Column('interaction_id', sa.String(length=50), nullable=True),
        sa.Column('channel', sa.String(length=50), nullable=True),
        sa.Column('actor', sa.String(length=50), nullable=True),
        sa.Column('notes', sa.Text(), nullable=True),
        sa.Column('metadata_json', sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(['booking_id'], ['trip_bookings.booking_id']),
        sa.ForeignKeyConstraint(['interaction_id'], ['interactions.interaction_id']),
        sa.ForeignKeyConstraint(['lead_id'], ['leads.lead_id']),
        sa.ForeignKeyConstraint(['traveler_id'], ['travelers.traveler_id']),
        sa.ForeignKeyConstraint(['trip_id'], ['trips.trip_id']),
        sa.PrimaryKeyConstraint('event_id'),
    )


def downgrade():
    op.drop_table('booking_event_trail')
