from __future__ import annotations
import sys
from pathlib import Path

CURRENT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = CURRENT_DIR.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.config import Settings
from app.sheets.google_sheets_api_gateway import GoogleSheetsApiGateway

def test_live_flow():
    settings = Settings()
    # Force backend to google_sheets if not already set
    settings.sheet_backend = "google_sheets"
    
    gateway = GoogleSheetsApiGateway(settings)
    
    print("--- 1. Testing Workbook Info ---")
    info = gateway.workbook_info()
    print(f"Workbook Info: {info}")

    print("\n--- 2. Testing CRM Preview (Read) ---")
    crm = gateway.crm_preview(limit=3)
    print(f"Found {len(crm)} travelers:")
    for t in crm:
        print(f" - {t['name']} ({t['id']})")

    print("\n--- 3. Testing Stats (Read) ---")
    stats = gateway.get_demo_stats()
    print(f"Stats: {stats}")

    print("\n--- 4. Testing Sales Cycle (Write) ---")
    # Using a unique name to avoid duplicates if possible
    test_name = f"Smoke Test User {int(Path(__file__).stat().st_mtime) % 10000}"
    result = gateway.run_sales_cycle(
        full_name=test_name,
        raw_phone="0123456789",
        country_code="20",
        trip_type="Local",
        channel="smoke-test",
        source="Gateway Test",
        agent_notes="Automated smoke test for live Sheets API."
    )
    print(f"Sales cycle completed for {test_name}")
    print(f"Action taken: {result.get('actions')}")
    print(f"Lead ID: {result.get('write_result', {}).get('lead_update', {}).get('lead_id')}")

    print("\n--- 5. Testing Booking (Write) ---")
    # Use the traveler ID from the sales cycle result if available
    traveler_id = result.get("traveler", {}).get("traveler_id") or "TR00001"
    
    # Attempt to draft a booking for the Siwa Demo trip
    try:
        booking = gateway.create_booking(
            traveler_id=traveler_id,
            traveler_name=test_name,
            trip_id="RT-LOC-26-900", # From demo seeds
            room_type="Single",
            channel="smoke-test",
            lead_id=result.get("write_result", {}).get("lead_update", {}).get("lead_id", ""),
            source="Smoke Test",
            agent_notes="Testing live booking write."
        )
        print(f"Booking created: {booking['booking_id']}")
    except Exception as e:
        print(f"Booking failed (maybe no capacity?): {e}")

    print("\n--- SMOKE TEST COMPLETE ---")

if __name__ == "__main__":
    try:
        test_live_flow()
    except Exception as e:
        print(f"ERROR: {e}")
        import traceback
        traceback.print_exc()
