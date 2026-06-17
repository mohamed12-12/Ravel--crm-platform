import pandas as pd
import os

excel_path = r'c:\Users\Mo\Desktop\nanovate tech\Projects\Rahma Traveler\RT - Travelers Database.xlsx'
if os.path.exists(excel_path):
    try:
        xl = pd.ExcelFile(excel_path)
        print(f"Sheets: {xl.sheet_names}")
        for sheet in xl.sheet_names:
            if sheet in ["Travelers", "Trips", "Trip Bookings", "Community Events", "CE Bookings", "DM Copy Library", "Language Templates"]:
                df = pd.read_excel(excel_path, sheet_name=sheet, nrows=5)
                print(f"\nSheet: {sheet}")
                print(f"Columns: {df.columns.tolist()}")
    except Exception as e:
        print(f"Error: {e}")
else:
    print("File not found")
