import pandas as pd
import logging
import os
from app.extensions import db
from app.models import Traveler, Trip, TripBooking, CommunityEvent, CEBooking, DMCopyLibrary, LanguageTemplate, Lead, Interaction

logger = logging.getLogger(__name__)

def run_full_import(file_path):
    """Imports from local Excel file."""
    xl = pd.ExcelFile(file_path)
    return _execute_import(lambda sheet, header: pd.read_excel(xl, sheet_name=sheet, header=header))

def run_sheets_import(sheet_id, credentials_path):
    """Imports from Google Sheets."""
    try:
        import gspread
        from google.oauth2.service_account import Credentials
    except ImportError:
        print("Error: gspread or google-auth not installed. Run: pip install gspread google-auth")
        return

    scopes = ['https://www.googleapis.com/auth/spreadsheets', 'https://www.googleapis.com/auth/drive']
    creds = Credentials.from_service_account_file(credentials_path, scopes=scopes)
    client = gspread.authorize(creds)
    sh = client.open_by_key(sheet_id)

    def get_sheet_df(sheet_name, header_idx):
        try:
            worksheet = sh.worksheet(sheet_name)
            data = worksheet.get_all_values()
            if not data: return pd.DataFrame()
            # Handle header offset
            headers = data[header_idx]
            rows = data[header_idx + 1:]
            return pd.DataFrame(rows, columns=headers)
        except Exception as e:
            logger.error(f"Error fetching sheet {sheet_name}: {e}")
            return pd.DataFrame()

    return _execute_import(get_sheet_df)

def _execute_import(df_loader_func):
    """Core import logic shared between Excel and Sheets."""
    import_config = [
        {"sheet": "Language Templates", "model": LanguageTemplate, "pk_db": "template_key", "pk_excel": "Template Key", "header": 0},
        {"sheet": "DM Copy Library", "model": DMCopyLibrary, "pk_db": "message_key", "pk_excel": "Message Key", "header": 0},
        {"sheet": "Trips", "model": Trip, "pk_db": "trip_id", "pk_excel": "Trip ID", "header": 1},
        {"sheet": "Community Events", "model": CommunityEvent, "pk_db": "event_id", "pk_excel": "Event ID", "header": 0},
        {"sheet": "Travelers", "model": Traveler, "pk_db": "traveler_id", "pk_excel": "Traveler ID", "header": 0},
        {"sheet": "Leads", "model": Lead, "pk_db": "lead_id", "pk_excel": "Lead ID", "header": 0},
        {"sheet": "Interactions", "model": Interaction, "pk_db": "interaction_id", "pk_excel": "Interaction ID", "header": 0},
        {"sheet": "Trip Bookings", "model": TripBooking, "pk_db": "booking_id", "pk_excel": "Booking ID", "header": 1},
        {"sheet": "CE Bookings", "model": CEBooking, "pk_db": "booking_id", "pk_excel": "Booking ID", "header": 0},
    ]
    
    results = {}
    
    for config in import_config:
        sheet_name = config["sheet"]
        model = config["model"]
        pk_db = config["pk_db"]
        pk_excel = config["pk_excel"]
        
        try:
            df = df_loader_func(sheet_name, config["header"])
            if df.empty:
                results[sheet_name] = 0
                continue
            
            # Fix column names for Trips because the sheet has duplicate headers
            # (Single, Double, Triple) for both Total and Remaining
            if sheet_name == "Trips" and len(df.columns) >= 13:
                cols = list(df.columns)
                cols[7] = "Single Total"
                cols[8] = "Double Total"
                cols[9] = "Triple Total"
                cols[10] = "Single Remaining"
                cols[11] = "Double Remaining"
                cols[12] = "Triple Remaining"
                df.columns = cols
            
            # Globally deduplicate any other duplicate column names to prevent Series errors
            df = df.loc[:, ~df.columns.duplicated()]
            
            # Replace all NaN/NaT/empty with None
            df = df.where(pd.notnull(df), None)
            df = df.replace('', None)
            
            # Fetch existing PKs and maps
            if model == LanguageTemplate:
                keys_in_df = []
                for _, row in df.iterrows():
                    t_key = row.get("Template Key")
                    t_lang = row.get("Language")
                    if t_key and t_lang:
                        keys_in_df.append((str(t_key).strip(), str(t_lang).strip()))
                
                if keys_in_df:
                    from sqlalchemy import or_
                    clauses = [db.and_(model.template_key == k, model.language == l) for k, l in keys_in_df]
                    existing_records = db.session.query(model).filter(or_(*clauses)).all()
                else:
                    existing_records = []
                existing_map = {(r.template_key, r.language): r for r in existing_records}
            else:
                keys_in_df = []
                for _, row in df.iterrows():
                    val = row.get(pk_excel)
                    if val is not None and val != "":
                        keys_in_df.append(str(val).strip())
                
                if keys_in_df:
                    existing_records = db.session.query(model).filter(getattr(model, pk_db).in_(keys_in_df)).all()
                else:
                    existing_records = []
                existing_map = {getattr(r, pk_db): r for r in existing_records}
            
            to_insert = []
            updates_count = 0
            
            for _, row in df.iterrows():
                # Clean row index
                if hasattr(row.index, 'str'):
                    row.index = row.index.str.strip()
                    
                instance = model.from_excel_row(row)
                
                if model in [TripBooking, CEBooking] and not getattr(instance, 'booking_id'):
                    import uuid
                    instance.booking_id = f"B-{uuid.uuid4().hex[:8].upper()}"
                
                if model == LanguageTemplate:
                    key = (instance.template_key, instance.language)
                else:
                    key = getattr(instance, pk_db)
                
                # If the parsed instance has no primary key (e.g. it was #N/A or blank), skip it
                if key is None or key == "":
                    continue
                
                data = {}
                for column in model.__table__.columns:
                    val = getattr(instance, column.name)
                    if val is not None:
                        data[column.name] = val
                
                if model == LanguageTemplate and 'id' in data:
                    del data['id']
                    
                if key in existing_map:
                    # Update existing record
                    existing_record = existing_map[key]
                    for col_name, val in data.items():
                        if col_name != pk_db and not (model == LanguageTemplate and col_name in ['template_key', 'language']):
                            setattr(existing_record, col_name, val)
                    updates_count += 1
                else:
                    # Insert new record
                    to_insert.append(data)
                    existing_map[key] = instance # Prevent duplicate inserts from same sheet
            
            if to_insert:
                db.session.bulk_insert_mappings(model, to_insert)
            
            db.session.commit()
            results[sheet_name] = len(to_insert) + updates_count
                
        except Exception as e:
            logger.error(f"Error importing sheet '{sheet_name}': {e}")
            db.session.rollback()
            results[sheet_name] = 0
            
    # Print summary
    print(f"Imported {results.get('Travelers', 0)} travelers, "
          f"{results.get('Leads', 0)} leads, "
          f"{results.get('Interactions', 0)} interactions, "
          f"{results.get('Trips', 0)} trips, "
          f"{results.get('Trip Bookings', 0)} trip bookings, "
          f"{results.get('Community Events', 0)} community events, "
          f"{results.get('CE Bookings', 0)} CE bookings, "
          f"{results.get('DM Copy Library', 0)} copy library entries, "
          f"{results.get('Language Templates', 0)} language templates.")
