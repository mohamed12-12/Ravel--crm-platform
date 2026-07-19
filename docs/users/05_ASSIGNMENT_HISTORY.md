# Assignment History

`AssignmentHistory` records lead and booking ownership changes with:

- resource type and ID
- previous user
- new user
- assigning employee
- timestamp
- optional reason
- optional request/session identifier

Lead and booking detail pages show this history. A deterministic legacy backfill records the new owner with no actor and the reason `Deterministic legacy assignment backfill`; this distinguishes historical migration from an employee action.

No assignment history is created when an update submits the same effective owner. This keeps the timeline meaningful and avoids duplicate events.
