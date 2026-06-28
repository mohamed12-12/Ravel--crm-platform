# app/routes/admin.py
from flask import (Blueprint, render_template, redirect, url_for,
                   flash, request, current_app, jsonify)
from app.extensions import db
from app.models.traveler import Traveler
from app.models.lead import Lead
from app.models.trip import Trip
from app.models.booking import TripBooking
from app.models.handoff import HandoffQueue
from app.services.importer import run_full_import, run_sheets_import
from app.services.identity import find_duplicates, merge_travelers
from services.crm.system_services.config import get_database_diagnostics
from datetime import datetime, timedelta, timezone
from pathlib import Path
import os
import uuid
from werkzeug.utils import secure_filename

admin_bp = Blueprint('admin', __name__, url_prefix='/admin')


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

    return render_template('admin/dashboard.html',
                           traveler_count=traveler_count,
                           active_lead_count=active_lead_count,
                           open_trip_count=open_trip_count,
                           pending_handoff_count=pending_handoff_count,
                           draft_booking_count=draft_booking_count,
                           recent_leads=recent_leads,
                           recent_handoffs=recent_handoffs,
                           urgent_leads=urgent_leads)


@admin_bp.route('/import', methods=['GET', 'POST'])
def import_data():
    results = None
    if request.method == 'POST':
        source = request.form.get('source', 'excel')

        if source == 'sheets':
            sheet_id = current_app.config.get('GOOGLE_SHEET_ID', '')
            creds_path = current_app.config.get('GOOGLE_CREDS_PATH', '')
            if not sheet_id or not creds_path:
                flash('Google Sheet ID or credentials path not configured in .env', 'error')
                return redirect(url_for('admin.import_data'))
            try:
                results = run_sheets_import(sheet_id, creds_path)
                flash('Google Sheets sync completed successfully.', 'success')
            except Exception as e:
                flash(f'Sheets sync failed: {e}', 'error')

        elif source == 'excel':
            f = request.files.get('excel_file')
            if not f or not f.filename:
                flash('No file uploaded.', 'error')
                return redirect(url_for('admin.import_data'))
            safe_name = secure_filename(f.filename) or f"import-{uuid.uuid4().hex}.xlsx"
            upload_root = Path(current_app.instance_path) / "uploads" / "imports"
            upload_root.mkdir(parents=True, exist_ok=True)
            upload_path = upload_root / f"{uuid.uuid4().hex}-{safe_name}"
            f.save(upload_path)
            try:
                results = run_full_import(upload_path)
                flash('Excel import completed successfully.', 'success')
            except Exception as e:
                flash(f'Excel import failed: {e}', 'error')

    sheet_id = current_app.config.get('GOOGLE_SHEET_ID', '')
    return render_template('admin/import.html', results=results, sheet_id=sheet_id)


@admin_bp.route('/duplicates')
def duplicates():
    try:
        dup_groups = find_duplicates(db.session)
    except Exception as e:
        dup_groups = []
        flash(f'Could not load duplicates: {e}', 'error')
    return render_template('admin/duplicates.html', dup_groups=dup_groups)


@admin_bp.route('/duplicates/merge', methods=['POST'])
def merge_dup():
    data = request.get_json()
    if not data:
        return jsonify({'error': 'No data'}), 400
    master_id = data.get('master_id')
    alias_ids = data.get('alias_ids', [])
    try:
        result = merge_travelers(master_id, alias_ids, db.session)
        return jsonify({'status': 'ok', 'details': result})
    except Exception as e:
        return jsonify({'error': str(e)}), 500


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
    """Return the active CRM database path and core table counts."""
    uri = current_app.config.get("SQLALCHEMY_DATABASE_URI", "")
    diagnostics = get_database_diagnostics()
    return jsonify(
        {
            "status": "ok",
            "sqlalchemyDatabaseUri": uri,
            "activeDbPath": diagnostics["db_path"],
            "counts": diagnostics["counts"],
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
