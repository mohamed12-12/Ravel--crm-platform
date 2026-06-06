# scripts/import_excel.py
import sys
import os

# Add the parent directory to sys.path so we can import 'app'
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from app import create_app
from app.services.importer import run_full_import

app = create_app()

def main():
    with app.app_context():
        # Path to the Excel file
        excel_path = 'data/travelers_database.xlsx'
        
        if not os.path.exists(excel_path):
            print(f"Error: Excel file not found at {excel_path}")
            return
            
        print(f"Starting full import from {excel_path}...")
        run_full_import(excel_path)
        print("Import process finished.")

if __name__ == '__main__':
    main()
