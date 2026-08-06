"""add_passport_and_currency_columns_reconciliation

Documents columns that already exist on the live production database (added
out-of-band, never captured by a migration) so a fresh database built from
`flask db upgrade` matches production, and so this is safe to run against
production itself where the columns already exist.

Revision ID: f4c8b21e9a3d
Revises: e1a7c9d4b826
Create Date: 2026-08-06 00:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


revision = "f4c8b21e9a3d"
down_revision = "e1a7c9d4b826"
branch_labels = None
depends_on = None


_TRAVELER_COLUMNS = [
    ("passport_name", sa.String(length=200)),
    ("passport_number", sa.String(length=50)),
    ("passport_expiry", sa.Date()),
    ("passport_nationality", sa.String(length=100)),
    ("passport_attachment_ref", sa.String(length=300)),
    ("preferred_currency", sa.String(length=20)),
]

# leads.passport_status is TEXT in production, not the VARCHAR(50) the
# SQLAlchemy model declares -- this migration matches live reality, not the
# model. See the audit note in this migration's PR/report.
_LEAD_COLUMNS = [
    ("passport_attachment_ref", sa.Text()),
    ("passport_status", sa.Text()),
]


def _add_column_if_missing(table_name: str, column: sa.Column) -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    existing = {col["name"] for col in inspector.get_columns(table_name)}
    if column.name not in existing:
        op.add_column(table_name, column)


def upgrade():
    for name, type_ in _TRAVELER_COLUMNS:
        _add_column_if_missing("travelers", sa.Column(name, type_, nullable=True))
    for name, type_ in _LEAD_COLUMNS:
        _add_column_if_missing("leads", sa.Column(name, type_, nullable=True))


def downgrade():
    with op.batch_alter_table("leads") as batch_op:
        for name, _ in _LEAD_COLUMNS:
            batch_op.drop_column(name)

    with op.batch_alter_table("travelers") as batch_op:
        for name, _ in _TRAVELER_COLUMNS:
            batch_op.drop_column(name)
