# Existing Traveler Workflow Audit

Date: 2026-07-15

## Source-of-Truth Evidence

| Rule area | Existing source |
| --- | --- |
| Phone normalization | `services/crm/system_services/phone_normalization.py::normalize_phone_input` |
| Identity resolution outcomes | `services/crm/system_services/unified_service.py::resolve_identity` |
| Duplicate-phone handling | `resolve_identity`, `archive/mvp-phase-artifacts/PHASE1_READONLY_AGENT_USAGE.md` |
| Archived/restricted traveler statuses | `apps/api/app/routes/travelers.py::ARCHIVE_LIKE_STATUSES` |
| Lead handoff stages | `apps/api/app/routes/leads.py` pipeline groups and transitions |
| Controlled write boundaries | `archive/mvp-phase-artifacts/PHASE2_CONTROLLED_AGENT_USAGE.md` |
| Read-only agent behavior | `archive/mvp-phase-artifacts/PHASE1_READONLY_AGENT_USAGE.md` |

## Status and Outcome Matrix

| Exact value | Meaning | Continue conversation | Trip search | Lead creation | Booking draft | Handoff | Customer-facing response | Implemented by |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `single_match` | One CRM traveler matched phone | Yes, after status policy | If traveler status permits | Later controlled phase only | Later controlled phase only | If `handoff_required` | Profile found | `UnifiedCRMService.resolve_identity` |
| `multiple_matches` | Duplicate phone records | Limited | No | No | No | Yes | Team review required | `resolve_identity`, Phase 1 docs |
| `not_found` | No traveler found | Collect profile details | No, until profile is safely created/verified | Not in current phase | No | No by default | New traveler details required | `resolve_identity`, Phase 2 docs |
| `Active` | Normal traveler status | Yes | Yes | Later controlled phase only | Later controlled phase only | No | Traveler verified | Traveler routes and seed data |
| `VIP` | VIP traveler status | Yes | Yes | Later controlled phase only | Later controlled phase only | No by default | Traveler verified; no invented discounts | `UnifiedCRMService.derive_lead_stage` |
| `Repeat` | Repeat traveler/customer tier | Yes | Yes | Later controlled phase only | Later controlled phase only | No by default | Traveler verified | `derive_lead_stage`, write executor |
| `inactive` | Archived-like traveler | Limited | No | No | No | Yes | Human review required | `ARCHIVE_LIKE_STATUSES` |
| `archived` | Archived-like traveler | Limited | No | No | No | Yes | Human review required | `ARCHIVE_LIKE_STATUSES` |
| `blacklisted` | Restricted traveler | Limited | No | No | No | Yes | Human review required | `ARCHIVE_LIKE_STATUSES`, lead route guards |
| `blocked` | Restricted traveler | Limited | No | No | No | Yes | Human review required | `ARCHIVE_LIKE_STATUSES`, lead routes |

## Current Agent Decision

The tool-calling runtime now treats CRM identity as the first workflow gate. Customer trip preferences are stored as customer-provided facts, but verified CRM facts only come from `ReadOnlyCRMTools` and existing CRM services.
