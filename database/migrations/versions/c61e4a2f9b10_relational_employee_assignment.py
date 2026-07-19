"""Add relational employee assignment and user audit tables.

Legacy ``assigned_to`` text values are intentionally retained.  Existing
records are only linked when the value matches one active or inactive user
username/full name exactly after whitespace/case normalization.
"""

from alembic import op
import sqlalchemy as sa
from datetime import datetime, timezone


revision = "c61e4a2f9b10"
down_revision = "b52e9d6a31f4"
branch_labels = None
depends_on = None


def _tables(bind):
    return set(sa.inspect(bind).get_table_names())


def _columns(bind, table_name):
    return {column["name"] for column in sa.inspect(bind).get_columns(table_name)}


def _add_columns(bind, table_name, columns):
    if table_name not in _tables(bind):
        return
    existing = _columns(bind, table_name)
    for name, column in columns.items():
        if name not in existing:
            with op.batch_alter_table(table_name) as batch_op:
                batch_op.add_column(column)


def _normalized(value):
    return " ".join(str(value or "").strip().casefold().split())


def _backfill(bind):
    users = bind.execute(sa.text("SELECT id, username, full_name FROM users")).mappings().all()
    by_name = {}
    for user in users:
        for value in (user["username"], user["full_name"]):
            key = _normalized(value)
            if key:
                by_name.setdefault(key, []).append(user["id"])

    now = datetime.now(timezone.utc).replace(microsecond=0)
    for table_name, id_column in (("leads", "lead_id"), ("trip_bookings", "booking_id")):
        if table_name not in _tables(bind):
            continue
        rows = bind.execute(sa.text(
            f"SELECT {id_column}, assigned_to FROM {table_name} "
            "WHERE assigned_to_user_id IS NULL AND assigned_to IS NOT NULL AND assigned_to <> ''"
        )).mappings().all()
        for row in rows:
            matches = set(by_name.get(_normalized(row["assigned_to"]), []))
            if len(matches) != 1:
                continue
            user_id = next(iter(matches))
            bind.execute(sa.text(
                f"UPDATE {table_name} SET assigned_to_user_id = :user_id, assigned_at = :assigned_at "
                f"WHERE {id_column} = :resource_id"
            ), {"user_id": user_id, "assigned_at": now, "resource_id": row[id_column]})
            bind.execute(sa.text(
                "INSERT INTO assignment_history "
                "(resource_type, resource_id, previous_user_id, new_user_id, reason, created_at) "
                "VALUES (:resource_type, :resource_id, NULL, :new_user_id, :reason, :created_at)"
            ), {
                "resource_type": "lead" if table_name == "leads" else "booking",
                "resource_id": str(row[id_column]),
                "new_user_id": user_id,
                "reason": "Deterministic legacy assignment backfill",
                "created_at": now,
            })


def upgrade():
    bind = op.get_bind()
    tables = _tables(bind)

    if "users" not in tables:
        op.create_table(
            "users",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("username", sa.String(length=80), nullable=False, unique=True),
            sa.Column("full_name", sa.String(length=200), nullable=True),
            sa.Column("password_hash", sa.String(length=200), nullable=False),
            sa.Column("email", sa.String(length=120), nullable=True, unique=True),
            sa.Column("role", sa.String(length=50), nullable=False, server_default="agent"),
            sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
            sa.Column("created_at", sa.DateTime(), nullable=True),
            sa.Column("updated_at", sa.DateTime(), nullable=True),
            sa.Column("last_login_at", sa.DateTime(), nullable=True),
        )
        tables.add("users")
    else:
        _add_columns(bind, "users", {
            "full_name": sa.Column("full_name", sa.String(length=200)),
            "role": sa.Column("role", sa.String(length=50), nullable=False, server_default="agent"),
            "updated_at": sa.Column("updated_at", sa.DateTime()),
            "last_login_at": sa.Column("last_login_at", sa.DateTime()),
        })

    _add_columns(bind, "leads", {
        "assigned_to_user_id": sa.Column("assigned_to_user_id", sa.Integer(), sa.ForeignKey("users.id", name="fk_leads_assigned_to_user")),
        "assigned_at": sa.Column("assigned_at", sa.DateTime()),
        "assigned_by_user_id": sa.Column("assigned_by_user_id", sa.Integer(), sa.ForeignKey("users.id", name="fk_leads_assigned_by_user")),
    })
    _add_columns(bind, "trip_bookings", {
        "assigned_to_user_id": sa.Column("assigned_to_user_id", sa.Integer(), sa.ForeignKey("users.id", name="fk_bookings_assigned_to_user")),
        "assigned_at": sa.Column("assigned_at", sa.DateTime()),
        "assigned_by_user_id": sa.Column("assigned_by_user_id", sa.Integer(), sa.ForeignKey("users.id", name="fk_bookings_assigned_by_user")),
    })

    tables = _tables(bind)
    if "assignment_history" not in tables:
        op.create_table(
            "assignment_history",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("resource_type", sa.String(length=30), nullable=False),
            sa.Column("resource_id", sa.String(length=80), nullable=False),
            sa.Column("previous_user_id", sa.Integer(), sa.ForeignKey("users.id")),
            sa.Column("new_user_id", sa.Integer(), sa.ForeignKey("users.id")),
            sa.Column("assigned_by_user_id", sa.Integer(), sa.ForeignKey("users.id")),
            sa.Column("reason", sa.Text()),
            sa.Column("request_id", sa.String(length=100)),
            sa.Column("created_at", sa.DateTime(), nullable=False),
        )
        op.create_index("ix_assignment_history_resource", "assignment_history", ["resource_type", "resource_id"])
    if "user_audit_log" not in tables:
        op.create_table(
            "user_audit_log",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("actor_user_id", sa.Integer(), sa.ForeignKey("users.id")),
            sa.Column("target_user_id", sa.Integer(), sa.ForeignKey("users.id")),
            sa.Column("action", sa.String(length=80), nullable=False),
            sa.Column("details", sa.Text()),
            sa.Column("created_at", sa.DateTime(), nullable=False),
        )

    for table_name, columns in (("leads", ("assigned_to_user_id", "assigned_by_user_id")),
                                ("trip_bookings", ("assigned_to_user_id", "assigned_by_user_id"))):
        if table_name in _tables(bind):
            for column_name in columns:
                index_name = f"ix_{table_name}_{column_name}"
                if index_name not in {index["name"] for index in sa.inspect(bind).get_indexes(table_name)}:
                    op.create_index(index_name, table_name, [column_name])

    _backfill(bind)


def downgrade():
    bind = op.get_bind()
    for table_name, columns in (("trip_bookings", ("assigned_by_user_id", "assigned_at", "assigned_to_user_id")),
                                ("leads", ("assigned_by_user_id", "assigned_at", "assigned_to_user_id"))):
        if table_name in _tables(bind):
            with op.batch_alter_table(table_name) as batch_op:
                existing = _columns(bind, table_name)
                for column_name in columns:
                    if column_name in existing:
                        batch_op.drop_column(column_name)
    if "user_audit_log" in _tables(bind):
        op.drop_table("user_audit_log")
    if "assignment_history" in _tables(bind):
        op.drop_index("ix_assignment_history_resource", table_name="assignment_history")
        op.drop_table("assignment_history")
