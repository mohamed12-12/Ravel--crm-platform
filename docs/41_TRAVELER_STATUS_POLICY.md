# Traveler Status Policy

Date: 2026-07-15

Traveler status is never inferred from model text. The agent may store status only when it comes from CRM context or a CRM tool result.

## Status Groups

| Status | Policy |
| --- | --- |
| `Active` | Continue to trip discovery after CRM lookup. |
| `VIP` | Continue to trip discovery, preserve VIP context, do not invent discounts. |
| `Repeat` | Continue to trip discovery, preserve repeat-customer context. |
| `inactive` | Stop traveler-specific automation and require human review. |
| `archived` | Stop traveler-specific automation and require human review. |
| `blacklisted` | Stop sales workflow and require human review. |
| `blocked` | Stop sales workflow and require human review. |

## Source Files

- `apps/api/app/routes/travelers.py::ARCHIVE_LIKE_STATUSES`
- `services/crm/system_services/unified_service.py::resolve_identity`
- `services/crm/system_services/unified_service.py::derive_lead_stage`
