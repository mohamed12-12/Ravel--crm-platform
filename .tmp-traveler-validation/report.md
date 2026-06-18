# Excel to CRM Migration Run Report

- Mode: execute
- Scope: travelers
- Workbook: `C:\Users\Mo\Desktop\nanovate tech\Projects\Rahma Traveler\RT - Travelers Database.xlsx`
- Database URI: `sqlite:///C:/Users/Mo/Desktop/nanovate tech/Projects/Rahma Traveler/.tmp-traveler-validation/crm.db`
- Quarantined rows: 191

## Summary

| Sheet | Scanned | Created | Updated | Skipped | Quarantined | Errors |
|---|---:|---:|---:|---:|---:|---:|
| Travelers | 773 | 571 | 0 | 0 | 191 | 0 |

## Quarantine Reasons

- missing_phone_and_name: 167
- decision_requires_manual_review: 16
- duplicate_phone_identity_in_workbook: 8

## Validation

- duplicate_traveler_ids: []
- duplicate_phone_lookup_keys: ['966:546052012', '20:1127311373', '20:1280003592', '20:1097850042', '20:1060035570', '20:1200644400']
- traveler_count: 571
