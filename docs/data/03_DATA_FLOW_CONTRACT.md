# Data Flow Contract

## Allowed flows

| Flow | Contract |
|---|---|
| CRM frontend | `Browser -> protected CRM route/service -> CRM DB` |
| AI-agent read | `Agent -> POST /api/crm/agent/read -> CRM service -> CRM DB` |
| AI-agent controlled write | `Agent -> POST /api/crm/agent/write -> validator -> CRM service -> CRM DB` |
| AI passport metadata | `Agent -> POST /api/crm/agent/passport -> CRM service -> CRM DB` |
| Excel import | `Workbook -> parse/normalize -> fingerprinted preview -> conflict review -> approved insert -> CRM DB` |
| Google import | `Sheet -> parse/normalize -> fingerprinted preview -> conflict review -> approved insert -> CRM DB` |
| CSV export | `CRM DB -> allowlisted serializer -> generated response` |
| Sheet export | `CRM DB -> explicit adapter -> runtime workbook or configured Google destination` |
| Demo reset | `Demo source workbook -> demo runtime workbook`, only with `DEMO_DATA_MODE=true` |

## Prohibited flows

- Excel and CRM uncontrolled bidirectional synchronization.
- Google Sheets as a fallback for live CRM facts.
- A GET page or export mutating CRM from spreadsheet data.
- Source workbook writes from normal CRM operations.
- Spreadsheet-generated IDs for new operational records.
- Spreadsheet status or inventory overwrites.
- Agent direct database access in production.
- Last-write-wins conflict resolution.
- Production reset of any operational database or workbook.

## Read rules

CRM values win whenever a CRM row exists. If an external row differs, the difference is a conflict, not a fresher value. An unavailable CRM API is an operational error; the agent must not silently fall back to a workbook.

## Write rules

All operational mutations must pass CRM validation. API clients send intent and proposed values, never authoritative IDs or protected transitions unless the operation explicitly accepts a validated legacy ID.

Optional sheet mirroring is disabled by default. When enabled, it is one-way export and targets the runtime workbook. `allow_source_workbook_writes` remains false in environment-derived settings.

## Failure rules

- API failure: return a controlled error and perform no alternative write.
- Import parse failure: classify the row or sheet invalid and leave CRM unchanged.
- Conflict: preserve CRM value and require review.
- Partial import failure: roll back the transaction.
- Export failure: do not mutate CRM and do not reuse a stale generated artifact as current data.

## Observability

Agent writes retain validator/audit results. Import previews write a JSON artifact under the CRM instance `import-staging` directory using a SHA-256 content fingerprint. Exports include data authority, schema version, and generation time headers.
