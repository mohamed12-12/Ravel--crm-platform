import sys
from pathlib import Path
from googleapiclient.discovery import build
from google.oauth2.service_account import Credentials

CURRENT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = CURRENT_DIR.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from services.ai_agent.ai_agent_app.config import load_settings

def convert_excel_to_sheets():
    settings = load_settings()
    creds_path = Path(settings.google_application_credentials)
    if not creds_path.is_absolute():
        creds_path = PROJECT_ROOT / creds_path

    print(f"Using credentials: {creds_path}")
    print(f"Target Excel File ID: {settings.google_sheet_id}")

    scopes = ['https://www.googleapis.com/auth/drive']
    creds = Credentials.from_service_account_file(str(creds_path), scopes=scopes)
    drive_service = build('drive', 'v3', credentials=creds)

    # 1. Get the original file metadata to preserve the name
    try:
        file_metadata = drive_service.files().get(fileId=settings.google_sheet_id).execute()
        original_name = file_metadata.get('name', 'Rahma Traveler Database')
        print(f"Found file: {original_name}")
    except Exception as e:
        print(f"Error finding original file: {e}")
        return

    # 2. Copy and convert the file
    # We use files().copy with a new mimeType to trigger conversion
    copied_file_metadata = {
        'name': f"{original_name} (Native Google Sheet)",
        'mimeType': 'application/vnd.google-apps.spreadsheet'
    }

    print("Converting to Google Sheet format...")
    try:
        new_file = drive_service.files().copy(
            fileId=settings.google_sheet_id,
            body=copied_file_metadata
        ).execute()

        new_id = new_file.get('id')
        print("\n" + "="*50)
        print("CONVERSION SUCCESSFUL!")
        print("="*50)
        print(f"New Native Google Sheet Name: {new_file.get('name')}")
        print(f"New GOOGLE_SHEET_ID: {new_id}")
        print("="*50)
        print("\nACTION REQUIRED:")
        print(f"1. Update your .env file with: GOOGLE_SHEET_ID={new_id}")
        print(f"2. Ensure you share the original Excel file (or the folder) with the service account email if you haven't already.")
        print(f"   Service Account: {creds.service_account_email}")
        print("="*50)

    except Exception as e:
        print(f"Conversion failed: {e}")
        print("\nPossible reasons:")
        print("1. The service account doesn't have permission to the file.")
        print("2. The file is already a Google Sheet (unlikely given your error).")

if __name__ == "__main__":
    convert_excel_to_sheets()
