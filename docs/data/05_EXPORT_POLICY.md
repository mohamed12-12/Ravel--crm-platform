# Export Policy

## Rules

1. Every export reads the CRM database at request time.
2. An export is a generated snapshot, never a writable authority.
3. Stable CRM IDs are included where the receiving contract allows them.
4. Secrets, authentication tokens, internal credentials, and unrestricted passport data are excluded.
5. Export fields are allowlisted per destination.
6. Values beginning with `=`, `+`, `-`, or `@` are neutralized for CSV formula safety.
7. Generated output identifies its schema version and generation time.
8. Export failure cannot trigger a spreadsheet-to-CRM refresh.

## Current traveler CSV

`GET /travelers/export` reads SQLAlchemy traveler records only. It no longer refreshes counters from Excel. The response includes:

- `X-Data-Authority: crm`
- `X-Data-Schema-Version`
- `X-Generated-At`

The field set excludes passport metadata, attachment paths, medical notes, emergency details, credentials, and internal audit fields.

## Excel and Google destinations

Sheet export requires both `CRM_SHEET_MIRROR_ENABLED=true` and the destination-specific export flag. It is disabled by default. Excel export targets the runtime workbook; environment-derived service settings forbid source-workbook writes.

Generated sheets must be labeled with generation metadata before they are distributed outside the current demo workflow. A consumer editing an export does not create an update until that file is submitted through the import preview and approval contract.
