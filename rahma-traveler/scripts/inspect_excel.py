# scripts/inspect_excel.py
import pandas as pd
import os

def main():
    excel_path = 'data/travelers_database.xlsx'
    if not os.path.exists(excel_path):
        print(f"Error: {excel_path} not found (Current Dir: {os.getcwd()})")
        return

    xl = pd.ExcelFile(excel_path)
    with open('inspect_results.txt', 'w', encoding='utf-8') as f:
        for sheet_name in xl.sheet_names:
            f.write(f"\n--- Sheet: {sheet_name} ---\n")
            try:
                df0 = pd.read_excel(excel_path, sheet_name=sheet_name, header=0, nrows=2)
                f.write(f"Header 0: {df0.columns.tolist()}\n")
                df1 = pd.read_excel(excel_path, sheet_name=sheet_name, header=1, nrows=2)
                f.write(f"Header 1: {df1.columns.tolist()}\n")
            except Exception as e:
                f.write(f"Error reading {sheet_name}: {e}\n")
    print("Results written to inspect_results.txt")

if __name__ == '__main__':
    main()
