# Import Policy

## Authority

Imports propose changes to the CRM. They never make Excel or Google Sheets authoritative.

## Required pipeline

1. Confirm the source adapter is enabled.
2. Read into a staging frame without changing operational rows.
3. Validate expected sheets and headers.
4. Normalize nulls, phones, dates, and supported values.
5. Require stable IDs for imported legacy records.
6. Detect duplicate keys and duplicate normalized phones.
7. Compare against current CRM values.
8. Produce a fingerprinted preview and audit artifact.
9. Obtain explicit approval for the exact fingerprint.
10. Apply permitted rows in one transaction.
11. Re-run comparison after apply to prove idempotency.

## Classifications

| Classification | Meaning | Default action |
|---|---|---|
| New | Stable ID is absent from CRM and identity checks pass | Candidate for approved insert |
| Update | External change is allowed by an entity-specific merge policy | Reserved; no generic auto-update currently allowed |
| Unchanged | Proposed fields equal CRM | No operation |
| Conflict | Existing CRM value differs | Preserve CRM; review required |
| Invalid | Parse, schema, ID, date, or required-field failure | Reject |
| Duplicate | Duplicate key or normalized phone identity conflict | Reject and create identity review input |

## Current enforcement

`apps/api/app/services/importer.py` defaults to preview. Existing rows are never updated by generic import. A development-only apply requires all of:

- `DIRECT_IMPORT_APPLY_ENABLED=true`
- a non-empty `approved_by`
- a preview artifact for the exact content fingerprint
- a row classified New

Apply is forbidden by production configuration validation. Replaying the same import does not duplicate the inserted ID. Blank booking or entity IDs are invalid; the importer no longer invents an ID.

## Conflict-sensitive fields

Status, trip dates, price, inventory, phone identity, booking state, passport state, and relationship IDs always require CRM-side review. Deleted external rows do not delete CRM rows.

## Rollback and audit

The current apply uses one database transaction and rolls back on failure. Preview artifacts contain source type, source label, content fingerprint, schema version, generation time, approver, counts, and conflict samples.

## Known limitation

The preview artifact is filesystem JSON rather than a relational sync-run, row, conflict, and approval model. Full schema-manifest validation, durable approval history, and operator rollback tooling remain production blockers. The safer historical CLI migration remains dry-run by default and should be used for controlled legacy migration planning.
