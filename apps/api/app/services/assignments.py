from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from flask import request
from sqlalchemy import text

from app.extensions import db
from app.models.assignment_history import AssignmentHistory
from app.models.booking import TripBooking
from app.models.lead import Lead
from app.models.user import User


ASSIGNABLE_ROLES = {'admin', 'manager', 'agent', 'sales'}


def _utc_now() -> datetime:
    return datetime.now(timezone.utc).replace(microsecond=0)


def normalize_name(value: str | None) -> str:
    return ' '.join(str(value or '').strip().casefold().split())


def active_assignees() -> list[User]:
    return (
        User.query
        .filter(User.is_active.is_(True), User.role.in_(ASSIGNABLE_ROLES))
        .order_by(User.full_name.asc(), User.username.asc())
        .all()
    )


def active_sales_assignees(*, for_update: bool = False) -> list[User]:
    query = (
        User.query
        .filter(User.is_active.is_(True), User.role == 'sales')
        .order_by(User.full_name.asc(), User.username.asc(), User.id.asc())
    )
    if for_update:
        query = query.with_for_update()
    return query.all()


def resolve_user_id(raw_value: Any, *, allow_blank: bool = True) -> int | None:
    raw = '' if raw_value is None else str(raw_value).strip()
    if not raw:
        if allow_blank:
            return None
        raise ValueError('An employee assignment is required')
    try:
        user_id = int(raw)
    except (TypeError, ValueError) as exc:
        raise ValueError('Choose a valid employee from the list') from exc

    user = db.session.get(User, user_id)
    if not user or not user.is_active or user.role not in ASSIGNABLE_ROLES:
        raise ValueError('That employee is not active or eligible for assignment')
    return user.id


def exact_legacy_user(value: str | None, *, include_inactive: bool = True) -> User | None:
    target = normalize_name(value)
    if not target:
        return None
    query = User.query
    if not include_inactive:
        query = query.filter(User.is_active.is_(True))
    matches = [
        user for user in query.all()
        if target in {normalize_name(user.username), normalize_name(user.full_name)}
    ]
    return matches[0] if len(matches) == 1 else None


def effective_user(resource) -> User | None:
    if resource.assigned_user:
        return resource.assigned_user
    return exact_legacy_user(resource.assigned_to, include_inactive=True)


def apply_assignment(
    resource,
    *,
    resource_type: str,
    resource_id: str,
    new_user_id: int | None,
    actor: User | None,
    reason: str = '',
    request_id: str = '',
) -> bool:
    previous_user = effective_user(resource)
    previous_id = previous_user.id if previous_user else resource.assigned_to_user_id
    if previous_id == new_user_id and (new_user_id or not resource.assigned_to):
        return False

    new_user = db.session.get(User, new_user_id) if new_user_id else None
    if new_user_id and (not new_user or not new_user.is_active or new_user.role not in ASSIGNABLE_ROLES):
        raise ValueError('That employee is not active or eligible for assignment')

    now = _utc_now()
    resource.assigned_to_user_id = new_user.id if new_user else None
    resource.assigned_to = new_user.display_name if new_user else None
    resource.assigned_at = now if new_user else None
    resource.assigned_by_user_id = actor.id if actor else None
    db.session.add(
        AssignmentHistory(
            resource_type=resource_type,
            resource_id=resource_id,
            previous_user_id=previous_id,
            new_user_id=new_user.id if new_user else None,
            assigned_by_user_id=actor.id if actor else None,
            reason=(reason or '').strip() or None,
            request_id=(request_id or request.headers.get('X-Request-ID', '')).strip() or None,
            created_at=now,
        )
    )
    return True


def assignment_history(resource_type: str, resource_id: str) -> list[AssignmentHistory]:
    return (
        AssignmentHistory.query
        .filter_by(resource_type=resource_type, resource_id=resource_id)
        .order_by(AssignmentHistory.created_at.asc(), AssignmentHistory.id.asc())
        .all()
    )


def next_round_robin_sales_assignee() -> User | None:
    sales_users = active_sales_assignees(for_update=True)
    if not sales_users:
        return None

    sales_ids = [user.id for user in sales_users]
    last_assigned_id = (
        db.session.query(AssignmentHistory.new_user_id)
        .join(User, User.id == AssignmentHistory.new_user_id)
        .filter(
            # One shared rotation across every auto-assigned resource type.
            # Private trip requests were assigned from the same sales pool but
            # excluded from this lookup, so they never advanced the cursor --
            # a run of private requests would all land on the same person while
            # the lead rotation carried on independently.
            AssignmentHistory.resource_type.in_(('lead', 'booking', 'private_trip_request')),
            AssignmentHistory.new_user_id.in_(sales_ids),
            User.is_active.is_(True),
            User.role == 'sales',
        )
        .order_by(AssignmentHistory.created_at.desc(), AssignmentHistory.id.desc())
        .with_for_update()
        .limit(1)
        .scalar()
    )
    if last_assigned_id not in sales_ids:
        return sales_users[0]

    next_index = (sales_ids.index(last_assigned_id) + 1) % len(sales_users)
    return sales_users[next_index]


def auto_assign_resource(
    resource,
    *,
    resource_type: str,
    resource_id: str,
    actor: User | None,
    reason: str = '',
) -> bool:
    next_user = next_round_robin_sales_assignee()
    if next_user is None:
        return False
    return apply_assignment(
        resource,
        resource_type=resource_type,
        resource_id=resource_id,
        new_user_id=next_user.id,
        actor=actor,
        reason=reason or 'Automatic round-robin sales assignment',
    )


def auto_assign_lead(resource, *, actor: User | None, reason: str = '') -> bool:
    return auto_assign_resource(
        resource,
        resource_type='lead',
        resource_id=resource.lead_id,
        actor=actor,
        reason=reason,
    )


def auto_assign_booking(resource, *, actor: User | None, reason: str = '') -> bool:
    return auto_assign_resource(
        resource,
        resource_type='booking',
        resource_id=resource.booking_id,
        actor=actor,
        reason=reason,
    )


def auto_assign_private_request(resource, *, actor: User | None, reason: str = '') -> bool:
    """Same round-robin ownership leads get, for private/custom trip requests.

    A private request created by the agent used to arrive Unassigned, so it sat
    in /admin/private-requests with nobody accountable for the 48-hour
    consultation SLA until a manager noticed and assigned it by hand.
    `resource_type` matches the string routes/private_requests.py already uses
    for this model's assignment history.
    """

    return auto_assign_resource(
        resource,
        resource_type='private_trip_request',
        resource_id=resource.request_id,
        actor=actor,
        reason=reason,
    )


def backfill_legacy_assignments() -> dict[str, int]:
    report = {'leads_matched': 0, 'bookings_matched': 0, 'ambiguous': 0, 'unmatched': 0}
    inspector = db.inspect(db.engine)
    table_names = set(inspector.get_table_names())
    users = User.query.all()
    for model, resource_type, id_field, timestamp_field, key in (
        (Lead, 'lead', 'lead_id', 'updated_at', 'leads_matched'),
        (TripBooking, 'booking', 'booking_id', 'draft_created_at', 'bookings_matched'),
    ):
        table_name = model.__tablename__
        if table_name not in table_names:
            continue
        columns = {column['name'] for column in inspector.get_columns(table_name)}
        required = {id_field, 'assigned_to', 'assigned_to_user_id', 'assigned_at'}
        if not required.issubset(columns):
            continue
        timestamp_available = timestamp_field in columns
        rows = db.session.execute(text(
            f"SELECT {id_field}, assigned_to, assigned_to_user_id"
            f"{', ' + timestamp_field if timestamp_available else ''} "
            f"FROM {table_name} WHERE assigned_to_user_id IS NULL "
            "AND assigned_to IS NOT NULL AND assigned_to <> ''"
        )).mappings().all()
        for row in rows:
            target = normalize_name(row['assigned_to'])
            candidates = [
                user for user in users
                if target in {normalize_name(user.username), normalize_name(user.full_name)}
            ]
            if len(candidates) == 1:
                assigned_at = (row.get(timestamp_field) if timestamp_available else None) or _utc_now()
                db.session.execute(text(
                    f"UPDATE {table_name} SET assigned_to_user_id = :user_id, assigned_at = :assigned_at "
                    f"WHERE {id_field} = :resource_id"
                ), {
                    'user_id': candidates[0].id,
                    'assigned_at': assigned_at,
                    'resource_id': row[id_field],
                })
                db.session.add(
                    AssignmentHistory(
                        resource_type=resource_type,
                        resource_id=str(row[id_field]),
                        previous_user_id=None,
                        new_user_id=candidates[0].id,
                        assigned_by_user_id=None,
                        reason='Deterministic legacy assignment backfill',
                        created_at=assigned_at,
                    )
                )
                report[key] += 1
            elif len(candidates) > 1:
                report['ambiguous'] += 1
            else:
                report['unmatched'] += 1
    if report['leads_matched'] or report['bookings_matched']:
        db.session.commit()
    return report
