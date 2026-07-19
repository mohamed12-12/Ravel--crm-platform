# Conflict Resolution Policy

## Principle

There is no last-write-wins behavior across CRM, Excel, and Google Sheets. CRM values remain operational until an explicit, audited review approves a change through CRM business rules.

## Resolution matrix

| Conflict | Detection | Outcome |
|---|---|---|
| Same phone, different traveler ID | Normalize phone and compare owner ID | Reject as Duplicate; create identity-review input; preserve CRM |
| Same traveler ID, different phone | Compare source fields to CRM | Conflict; verify identity before any change |
| Different traveler/trip/lead/booking/handoff status | Field comparison | Conflict; CRM transition review required |
| Different trip dates | Field comparison | Conflict; preserve CRM and review downstream bookings |
| Different price | Field comparison | Conflict; preserve CRM and require commercial approval |
| Different inventory | Field comparison plus booking state | Conflict; preserve CRM; never merge counts arithmetically |
| Missing CRM record | Stable ID and relationship validation | New candidate if valid; otherwise Invalid |
| Deleted external row | Absence comparison | No CRM deletion or cancellation |
| Stale spreadsheet version | Exact content fingerprint lacks a preview | Reject apply; generate a new preview |
| Concurrent CRM edit after preview | Recompare before apply | Reclassify as Conflict; do not overwrite |
| Duplicate source key | In-file key set | Duplicate; reject later occurrence |
| Safe descriptive field update | Entity-specific allowlist | Future Update classification after approval; generic importer currently conflicts |

## Merge rules

Safe automatic merges are intentionally empty in the generic importer. Entity-specific migration code may normalize formatting without changing meaning, but phone identity, IDs, statuses, relationships, dates, prices, inventory, passport data, and booking fields always require review.

## Review evidence

A review record must identify source, fingerprint, schema version, row/key, CRM value, proposed value, classification, reviewer, decision, time, and apply result. The current JSON preview stores most source and classification evidence but lacks a durable review decision table; this remains a production blocker.

## Failure and retry

Retries use the same source fingerprint. A changed file is a new import run. An already inserted ID is re-read from CRM and cannot be inserted again. Failed transactions roll back; operators do not repair partial rows in a spreadsheet and call that synchronized.
