# Data Authority Implementation

## Implemented controls

| Area | Implementation |
|---|---|
| Central authority | `services/data_authority.py` parses flags and validates startup ambiguity |
| CRM app config | `apps/api/app/config.py` and `app/__init__.py` fail fast on invalid authority settings |
| Agent config | `ai_agent_app/config.py` requires API/tool-calling production access |
| Agent API | Authenticated read, controlled write, and passport metadata endpoints in `routes/crm.py` |
| Agent clients | `CRMApiClient`, API-aware read tools, write executor, and passport bridge |
| Stale read removal | Traveler list/export and Google preview/stat paths read CRM first and do not hydrate CRM from sheets |
| Import safety | Generic importer is preview-first, fingerprinted, conflict-preserving, transactional, and development-gated |
| Export safety | CRM-only traveler CSV, formula neutralization, schema and generation headers |
| Sheet writes | Disabled by default; runtime-only when explicitly enabled; source writes disabled in environment settings |
| Demo isolation | Reset requires `DEMO_DATA_MODE=true` |
| Tests | `tests/test_data_authority.py` plus aligned isolation/security fixtures |

## Environment contract

```dotenv
DATA_AUTHORITY=crm
DATA_SCHEMA_VERSION=1
DATABASE_URL=
RAHMA_SYSTEM_DB_PATH=
CRM_ACCESS_MODE=api
CRM_API_BASE_URL=http://crm-service:5000
CRM_API_TOKEN=
EXCEL_IMPORT_ENABLED=true
EXCEL_EXPORT_ENABLED=true
GOOGLE_SHEETS_IMPORT_ENABLED=false
GOOGLE_SHEETS_EXPORT_ENABLED=false
DIRECT_IMPORT_APPLY_ENABLED=false
CRM_SHEET_MIRROR_ENABLED=false
DEMO_DATA_MODE=false
AI_AGENT_MODE=tool_calling
```

Development may set `CRM_ACCESS_MODE=shared_service` only when both database path settings resolve to the same SQLite file. `CRM_API_TOKEN` is shared by the agent client and CRM auth layer in the current deployment model.

## Compatibility retained

- Legacy deterministic behavior remains available for development and regression tests.
- Existing Excel and Google adapters remain present.
- The source workbook remains readable for approved previews and demo copy/content needs.
- Existing legacy migration tooling remains dry-run first.
- No CRM business model or historical document migration was performed.

## Tests added for requested controls

The focused suite covers CRM authority validation, API reads and writes, passport API routing, spreadsheet conflict preservation, Google import disablement, CRM-only export, reset isolation, path isolation, multiple-authority rejection, stable legacy IDs, CRM-only new IDs, duplicate phone conflicts, status/inventory conflicts, stale previews, idempotent replay, preview audit artifacts, and operational DB protection. Existing CRM/agent suites provide the broader regression coverage.

## Remaining production blockers

1. Replace filesystem import previews with relational sync-run/conflict/approval records.
2. Add formal schema manifests and supported-version negotiation for every adapter.
3. Centralize all CRM ID allocators and add database uniqueness/retry semantics.
4. Generalize the CRM agent adapter beyond SQLite.
5. Move attachment binaries to managed object storage and pass an opaque CRM-owned object reference.
6. Move operational data to a production database with inventory concurrency controls.
7. Add durable audit/history parity for lead and handoff status transitions.

## Deployment recommendation

**No-go for deployment hardening.** The authority decision and minimum ambiguity guards are ready for continued local integration and migration rehearsal. The blockers above must be completed and re-audited before production hardening begins.
