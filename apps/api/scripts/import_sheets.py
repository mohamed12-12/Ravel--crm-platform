# scripts/import_sheets.py
import sys
import os
from dotenv import load_dotenv

# Add parent directory to sys.path
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

# Load .env from project root
env_path = os.path.abspath(os.path.join(os.path.dirname(__file__), '../../.env'))
load_dotenv(env_path)

from app import create_app
from app.services.importer import run_sheets_import

app = create_app()

def main():
    sheet_id = os.getenv('GOOGLE_SHEET_ID')
    creds_file = os.getenv('GOOGLE_SERVICE_ACCOUNT_JSON')
    
    # Resolve relative path for credentials if needed
    if creds_file and not os.path.isabs(creds_file):
        creds_file = os.path.abspath(os.path.join(os.path.dirname(__file__), '../../', creds_file))

    if not sheet_id or not creds_file:
        print("Error: GOOGLE_SHEET_ID or GOOGLE_SERVICE_ACCOUNT_JSON not set in .env")
        return

    if not os.path.exists(creds_file):
        print(f"Error: Credentials file not found at {creds_file}")
        return

    print(f"Starting import from Google Sheet: {sheet_id}...")
    with app.app_context():
        run_sheets_import(sheet_id, creds_file)
    print("Import process finished.")

if __name__ == '__main__':
    main()
