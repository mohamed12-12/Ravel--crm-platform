# app/routes/admin.py
from flask import (Blueprint, render_template, redirect, url_for,
                   flash, request, current_app, jsonify)
from app.extensions import db
from app.models.traveler import Traveler
from app.models.lead import Lead
from app.models.trip import Trip
from app.models.booking import TripBooking
from app.models.handoff import HandoffQueue
from app.models.user import User
from app.models.user_audit import UserAuditLog
from app.models.booking_status_history import BookingStatusHistory
from services.crm.system_services.config import get_database_diagnostics
from datetime import datetime, timedelta, timezone
from sqlalchemy import or_
from werkzeug.security import generate_password_hash
from app.security import current_user, current_user_id, has_permission, permission_required

admin_bp = Blueprint('admin', __name__, url_prefix='/admin')
ROLE_OPTIONS = ['admin', 'manager', 'agent', 'sales']


def _audit_user_change(action: str, target: User, details: str = '') -> None:
    actor = current_user()
    db.session.add(UserAuditLog(
        actor_user_id=actor.id if actor else None,
        target_user_id=target.id,
        action=action,
        details=details or None,
    ))


@admin_bp.route('/users')
@permission_required('manage_users')
def users():
    user_rows = User.query.order_by(User.is_active.desc(), User.full_name.asc(), User.username.asc()).all()
    return render_template('admin/users.html', users=user_rows, roles=ROLE_OPTIONS)


@admin_bp.route('/users/create', methods=['POST'])
@permission_required('manage_users')
def create_user():
    username = (request.form.get('username') or '').strip()
    full_name = (request.form.get('full_name') or '').strip()
    email = (request.form.get('email') or '').strip() or None
    role = (request.form.get('role') or 'agent').strip().lower()
    password = request.form.get('password') or ''
    if not username or not password or len(password) < 8:
        flash('Username and a password of at least 8 characters are required.', 'error')
        return redirect(url_for('admin.users'))
    if role not in ROLE_OPTIONS:
        flash('Invalid employee role.', 'error')
        return redirect(url_for('admin.users'))
    if User.query.filter(db.func.lower(User.username) == username.casefold()).first():
        flash('That username already exists.', 'error')
        return redirect(url_for('admin.users'))
    if email and User.query.filter(db.func.lower(User.email) == email.casefold()).first():
        flash('That email already exists.', 'error')
        return redirect(url_for('admin.users'))
    user = User(
        username=username,
        full_name=full_name or username,
        email=email,
        role=role,
        is_active=True,
        password_hash=generate_password_hash(password),
    )
    db.session.add(user)
    db.session.flush()
    _audit_user_change('user_created', user, f'role={role}')
    db.session.commit()
    try:
        from app.services.assignments import backfill_legacy_assignments
        backfill_legacy_assignments()
    except Exception:
        db.session.rollback()
    flash(f'Employee {user.display_name} created.', 'success')
    return redirect(url_for('admin.users'))


@admin_bp.route('/users/<int:user_id>/update', methods=['POST'])
@permission_required('manage_users')
def update_user(user_id: int):
    user = db.get_or_404(User, user_id)
    actor = current_user()
    role = (request.form.get('role') or user.role).strip().lower()
    if role not in ROLE_OPTIONS:
        flash('Invalid employee role.', 'error')
        return redirect(url_for('admin.users'))
    if actor and actor.id == user.id and role != user.role:
        flash('You cannot change your own role.', 'error')
        return redirect(url_for('admin.users'))
    user.full_name = (request.form.get('full_name') or user.full_name or user.username).strip()
    user.email = (request.form.get('email') or '').strip() or None
    user.role = role
    _audit_user_change('user_updated', user, f'role={role}')
    db.session.commit()
    flash('Employee details updated.', 'success')
    return redirect(url_for('admin.users'))


@admin_bp.route('/users/<int:user_id>/toggle', methods=['POST'])
@permission_required('manage_users')
def toggle_user(user_id: int):
    user = db.get_or_404(User, user_id)
    actor = current_user()
    if actor and actor.id == user.id:
        flash('You cannot deactivate your own account.', 'error')
        return redirect(url_for('admin.users'))
    user.is_active = not bool(user.is_active)
    _audit_user_change('user_activated' if user.is_active else 'user_deactivated', user)
    db.session.commit()
    flash(f'Employee {user.display_name} is now {"active" if user.is_active else "inactive"}.', 'success')
    return redirect(url_for('admin.users'))


@admin_bp.route('/users/<int:user_id>/reset-password', methods=['POST'])
@permission_required('manage_users')
def reset_user_password(user_id: int):
    user = db.get_or_404(User, user_id)
    password = request.form.get('password') or ''
    if len(password) < 8:
        flash('Password must be at least 8 characters.', 'error')
        return redirect(url_for('admin.users'))
    user.password_hash = generate_password_hash(password)
    _audit_user_change('password_reset', user)
    db.session.commit()
    flash(f'Password reset for {user.display_name}.', 'success')
    return redirect(url_for('admin.users'))


@admin_bp.route('/users/<int:user_id>/delete', methods=['POST'])
@permission_required('manage_users')
def delete_user(user_id: int):
    from app.models.assignment_history import AssignmentHistory
    from app.models.trip_media import TripMedia

    user = db.get_or_404(User, user_id)
    actor = current_user()
    if actor and actor.id == user.id:
        flash('You cannot delete your own account while logged in as them.', 'error')
        return redirect(url_for('admin.users'))

    lead_count = user.assigned_leads.count()
    booking_count = user.assigned_bookings.count()
    if lead_count or booking_count:
        flash(
            f'Reassign {lead_count} lead(s) and {booking_count} booking(s) before deleting {user.display_name}.',
            'error',
        )
        return redirect(url_for('admin.users'))

    username = user.username
    display_name = user.display_name
    role = user.role

    # user.id is about to stop existing -- every other FK reference to it
    # (none of which represent a *current* assignment, since that was
    # already checked above) must be detached first, the same discipline
    # travelers.py's delete() uses for its own dependent tables. These are
    # historical/audit references, not active work, so they're nulled out
    # in place rather than deleted -- the row and its context (reason,
    # timestamp, resource) stay, only the dangling user link is dropped.
    UserAuditLog.query.filter(UserAuditLog.actor_user_id == user.id).update({'actor_user_id': None})
    UserAuditLog.query.filter(UserAuditLog.target_user_id == user.id).update({'target_user_id': None})
    AssignmentHistory.query.filter(AssignmentHistory.previous_user_id == user.id).update({'previous_user_id': None})
    AssignmentHistory.query.filter(AssignmentHistory.new_user_id == user.id).update({'new_user_id': None})
    AssignmentHistory.query.filter(AssignmentHistory.assigned_by_user_id == user.id).update({'assigned_by_user_id': None})
    Lead.query.filter(Lead.assigned_by_user_id == user.id).update({'assigned_by_user_id': None})
    TripBooking.query.filter(TripBooking.assigned_by_user_id == user.id).update({'assigned_by_user_id': None})
    TripMedia.query.filter(TripMedia.uploaded_by_user_id == user.id).update({'uploaded_by_user_id': None})

    db.session.add(UserAuditLog(
        actor_user_id=actor.id if actor else None,
        target_user_id=None,
        action='user_deleted',
        details=f'username={username} full_name={display_name} role={role}',
    ))
    db.session.delete(user)
    db.session.commit()
    flash(f'Employee {display_name} deleted.', 'success')
    return redirect(url_for('admin.users'))


@admin_bp.route('/')
def index():
    return redirect(url_for('admin.dashboard'))


@admin_bp.route('/dashboard')
def dashboard():
    # KPI counts
    traveler_count = Traveler.query.count()
    active_lead_count = Lead.query.filter(
        Lead.lead_stage.notin_(['Booked', 'Lost'])).count()
    open_trip_count = Trip.query.filter_by(sales_status='Open').count()
    pending_handoff_count = HandoffQueue.query.filter(
        HandoffQueue.status.in_(['Pending', 'New'])).count()
    draft_booking_count = TripBooking.query.filter_by(booking_status='Draft').count()

    # Recent leads (last 7 days)
    week_ago = datetime.now(timezone.utc) - timedelta(days=7)
    recent_leads = Lead.query.filter(Lead.created_at >= week_ago)\
        .order_by(Lead.created_at.desc()).limit(8).all()

    # Recent handoffs
    recent_handoffs = HandoffQueue.query.order_by(
        HandoffQueue.created_at.desc()).limit(6).all()

    # High priority leads
    urgent_leads = Lead.query.filter(
        Lead.priority == 'High',
        Lead.lead_stage.notin_(['Booked', 'Lost'])
    ).order_by(Lead.created_at.desc()).limit(5).all()
    today = datetime.now(timezone.utc).date()
    overdue_followups = Lead.query.filter(
        Lead.follow_up_due_date < today,
        Lead.lead_stage.notin_(['Won', 'Lost'])
    ).order_by(Lead.follow_up_due_date.asc()).limit(8).all()
    due_today = Lead.query.filter(
        Lead.follow_up_due_date == today,
        Lead.lead_stage.notin_(['Won', 'Lost'])
    ).order_by(Lead.updated_at.desc()).limit(8).all()
    new_unassigned = Lead.query.filter(
        Lead.lead_stage.in_(['New Lead', 'New', 'New Inquiry']),
        Lead.assigned_to_user_id.is_(None)
    ).order_by(Lead.created_at.desc()).limit(8).all()
    waiting_customer_leads = Lead.query.filter(
        Lead.lead_stage.in_(['Waiting Customer Reply', 'Follow Up Needed', 'VIP Follow Up', 'Repeat Follow Up'])
    ).order_by(Lead.updated_at.desc()).limit(8).all()
    deposit_followups = TripBooking.query.filter(
        or_(TripBooking.booking_status == 'Payment Pending', TripBooking.payment_status == 'Pending')
    ).order_by(TripBooking.draft_created_at.desc()).limit(8).all()
    missing_documents = TripBooking.query.filter(
        TripBooking.passport_status == 'pending'
    ).order_by(TripBooking.draft_created_at.desc()).limit(8).all()
    my_leads = Lead.query.filter(Lead.assigned_to_user_id == current_user_id()).order_by(Lead.updated_at.desc()).limit(8).all() if current_user_id() else []
    my_bookings = TripBooking.query.filter(TripBooking.assigned_to_user_id == current_user_id()).order_by(TripBooking.draft_created_at.desc()).limit(8).all() if current_user_id() else []
    my_overdue_leads = Lead.query.filter(
        Lead.assigned_to_user_id == current_user_id(),
        Lead.follow_up_due_date < today,
        Lead.lead_stage.notin_(['Won', 'Lost']),
    ).order_by(Lead.follow_up_due_date.asc()).limit(8).all() if current_user_id() else []
    inactive_owner_leads = Lead.query.filter(Lead.assigned_user.has(User.is_active.is_(False))).limit(8).all() if has_permission('view_all') else []
    inactive_owner_bookings = TripBooking.query.filter(TripBooking.assigned_user.has(User.is_active.is_(False))).limit(8).all() if has_permission('view_all') else []
    team_workload = []
    if has_permission('view_all'):
        for employee in User.query.filter(User.is_active.is_(True)).order_by(User.full_name.asc()).all():
            team_workload.append({
                'employee': employee,
                'leads': Lead.query.filter_by(assigned_to_user_id=employee.id).count(),
                'bookings': TripBooking.query.filter_by(assigned_to_user_id=employee.id).count(),
            })
    recent_bookings = TripBooking.query.order_by(TripBooking.draft_created_at.desc()).limit(8).all()
    recent_login_activity = (
        UserAuditLog.query
        .filter(UserAuditLog.action == 'login')
        .order_by(UserAuditLog.created_at.desc())
        .limit(8)
        .all()
    )

    return render_template('admin/dashboard.html',
                           traveler_count=traveler_count,
                           active_lead_count=active_lead_count,
                           open_trip_count=open_trip_count,
                           pending_handoff_count=pending_handoff_count,
                           draft_booking_count=draft_booking_count,
                           recent_leads=recent_leads,
                           recent_handoffs=recent_handoffs,
                           urgent_leads=urgent_leads,
                           overdue_followups=overdue_followups,
                           due_today=due_today,
                           new_unassigned=new_unassigned,
                           waiting_customer_leads=waiting_customer_leads,
                           deposit_followups=deposit_followups,
                           missing_documents=missing_documents,
                           my_leads=my_leads,
                           my_bookings=my_bookings,
                           my_overdue_leads=my_overdue_leads,
                           inactive_owner_leads=inactive_owner_leads,
                           inactive_owner_bookings=inactive_owner_bookings,
                           team_workload=team_workload,
                           recent_bookings=recent_bookings,
                           recent_login_activity=recent_login_activity)


@admin_bp.route('/revenue-analytics')
def revenue_analytics():
    """Company-wide revenue by year/month. Admin-only (see
    ADMIN_ROLE_ONLY_ENDPOINTS in app/security.py) since this aggregates
    money across every traveler, not just the ones an employee owns.
    Reuses app.services.revenue's booking_revenue()/booking_recognized_at()
    so this can never silently disagree with the per-traveler Lifetime
    Revenue figure on travelers/detail.html about what counts as revenue.
    """
    from app.services.revenue import booking_recognized_at, booking_revenue

    now = datetime.now(timezone.utc)
    try:
        selected_year = int(request.args.get('year') or now.year)
    except (TypeError, ValueError):
        selected_year = now.year

    bookings = TripBooking.query.all()
    trip_ids = {b.trip_id for b in bookings if b.trip_id}
    trips = {t.trip_id: t for t in Trip.query.filter(Trip.trip_id.in_(trip_ids)).all()} if trip_ids else {}

    history_by_booking: dict[str, list] = {}
    for entry in BookingStatusHistory.query.order_by(BookingStatusHistory.changed_at.asc()).all():
        history_by_booking.setdefault(entry.booking_id, []).append(entry)

    yearly: dict[int, dict[str, float]] = {}
    monthly: dict[int, dict[str, float]] = {i: {"USD": 0.0, "EGP": 0.0, "count": 0} for i in range(1, 13)}
    by_trip_type: dict[str, dict[str, float]] = {}
    available_years: set[int] = {now.year}
    total = {"USD": 0.0, "EGP": 0.0, "count": 0}
    this_month = {"USD": 0.0, "EGP": 0.0}

    for booking in bookings:
        trip = trips.get(booking.trip_id)
        result = booking_revenue(booking, trip)
        if result is None:
            continue
        currency, amount = result
        recognized_at = booking_recognized_at(booking, history_by_booking)
        if not recognized_at:
            continue
        year = recognized_at.year
        available_years.add(year)

        bucket = yearly.setdefault(year, {"USD": 0.0, "EGP": 0.0, "count": 0})
        bucket[currency] += amount
        bucket["count"] += 1

        total[currency] += amount
        total["count"] += 1

        if year == now.year and recognized_at.month == now.month:
            this_month[currency] += amount

        if year == selected_year:
            month_bucket = monthly[recognized_at.month]
            month_bucket[currency] += amount
            month_bucket["count"] += 1

        trip_type = str(trip.type).strip().title() if trip and trip.type else "Unspecified"
        type_bucket = by_trip_type.setdefault(trip_type, {"USD": 0.0, "EGP": 0.0, "count": 0})
        type_bucket[currency] += amount
        type_bucket["count"] += 1

    years_sorted = sorted(available_years, reverse=True)
    yearly_rows = [
        {"year": year, **yearly.get(year, {"USD": 0.0, "EGP": 0.0, "count": 0})}
        for year in years_sorted
    ]
    monthly_rows = [
        {"month": month, "label": datetime(2000, month, 1).strftime("%b"), **monthly[month]}
        for month in range(1, 13)
    ]
    max_monthly_usd = max((row["USD"] for row in monthly_rows), default=0.0) or 1.0
    max_monthly_egp = max((row["EGP"] for row in monthly_rows), default=0.0) or 1.0
    for row in monthly_rows:
        row["usd_bar_pct"] = round(min(row["USD"] / max_monthly_usd, 1.0) * 100, 1)
        row["egp_bar_pct"] = round(min(row["EGP"] / max_monthly_egp, 1.0) * 100, 1)

    trip_type_rows = sorted(by_trip_type.items(), key=lambda item: -(item[1]["USD"] + item[1]["EGP"]))

    return render_template(
        'admin/revenue_analytics.html',
        total=total,
        this_year=yearly.get(now.year, {"USD": 0.0, "EGP": 0.0, "count": 0}),
        this_month=this_month,
        yearly_rows=yearly_rows,
        monthly_rows=monthly_rows,
        trip_type_rows=trip_type_rows,
        selected_year=selected_year,
        available_years=years_sorted,
        current_year=now.year,
        current_month_label=now.strftime("%B %Y"),
    )


from app.models.copy_library import DMCopyLibrary, LanguageTemplate

@admin_bp.route('/copy-library')
def copy_library():
    dm_copies = DMCopyLibrary.query.order_by(DMCopyLibrary.flow_key, DMCopyLibrary.step_key).all()
    lang_templates = LanguageTemplate.query.order_by(LanguageTemplate.template_key).all()
    return render_template('admin/copy_library.html', dm_copies=dm_copies, lang_templates=lang_templates)

@admin_bp.route('/debug')
def debug_trips_headers():
    sheet_id = current_app.config.get('GOOGLE_SHEET_ID', '')
    creds_path = current_app.config.get('GOOGLE_CREDS_PATH', '')
    if not sheet_id or not creds_path:
        return "No creds", 400
    try:
        import gspread
        from google.oauth2.service_account import Credentials
        scopes = ['https://www.googleapis.com/auth/spreadsheets', 'https://www.googleapis.com/auth/drive']
        creds = Credentials.from_service_account_file(creds_path, scopes=scopes)
        client = gspread.authorize(creds)
        sh = client.open_by_key(sheet_id)
        worksheet = sh.worksheet("Trips")
        data = worksheet.get_all_values()
        return jsonify({"row1": data[0], "row2": data[1], "row3": data[2]})
    except Exception as e:
        return str(e), 500


@admin_bp.route('/db-health')
def db_health():
    """Return the active CRM database path/backend and core table counts.

    get_database_diagnostics() always reads the legacy SQLite file directly
    (services/crm/system_services), so it can't be used as-is once
    SQLALCHEMY_DATABASE_URI points at Postgres - it would silently report
    stale SQLite counts while the app is actually serving Postgres data.
    Query the live SQLAlchemy session instead in that case, mirroring the
    backend check in app/routes/crm.py's _agent_runtime().
    """
    uri = current_app.config.get("SQLALCHEMY_DATABASE_URI", "")
    if str(uri).startswith("sqlite:///"):
        diagnostics = get_database_diagnostics()
        active_db = diagnostics["db_path"]
        counts = diagnostics["counts"]
    else:
        active_db = uri
        counts = {
            "travelers": Traveler.query.count(),
            "trips": Trip.query.count(),
            "trip_bookings": TripBooking.query.count(),
            "booking_status_history": BookingStatusHistory.query.count(),
            "leads": Lead.query.count(),
        }
    return jsonify(
        {
            "status": "ok",
            "sqlalchemyDatabaseUri": uri,
            "activeDbPath": active_db,
            "counts": counts,
        }
    )


@admin_bp.route('/sync-issues')
def sync_issues():
    from sqlalchemy import text
    result = db.session.execute(text("SELECT * FROM sync_queue ORDER BY updated_at DESC")).fetchall()
    issues = [dict(row._mapping) for row in result]
    return render_template('admin/sync_issues.html', issues=issues)


@admin_bp.route('/sync-issues/retry', methods=['POST'])
def retry_sync():
    data = request.get_json()
    if not data:
        return jsonify({'error': 'No data provided'}), 400
    
    mapping_name = data.get('mapping_name')
    record_id = data.get('record_id')
    
    try:
        from services.crm.system_services import UnifiedCRMService
        service = UnifiedCRMService()
        
        if mapping_name == "Trips":
            res = service.sync_trip_to_sheet(record_id)
        else:
            res = service.sync_record_to_sheet(mapping_name, record_id)
            
        if res.get('status') == 'ok':
            return jsonify({'status': 'ok'})
        else:
            return jsonify({'error': res.get('reason', 'Unknown sync failure')}), 500
    except Exception as e:
        return jsonify({'error': str(e)}), 500
