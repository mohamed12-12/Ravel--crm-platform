from app.config import get_settings
from app.sheets.google_sheets_api_gateway import GoogleSheetsApiGateway

settings = get_settings()
gw = GoogleSheetsApiGateway(settings)
ws = gw._get_ws("Trips")
all_values = ws.get_all_values()
print("Row 1 (idx 0):", all_values[0])
print("Row 2 (idx 1):", all_values[1])
print("Row 3 (idx 2):", all_values[2])
