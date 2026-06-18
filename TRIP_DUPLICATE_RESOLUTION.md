# Trip Duplicate Resolution

Scope: analysis only. No trip rows were imported, deleted, merged, renamed, or assigned new IDs during this review.

Source inspected:

- Workbook: `RT - Travelers Database.xlsx`
- Sheet: `Trips`
- Header row: 2
- Target duplicate IDs: `RT-INT-25-002`, `RT-INT-25-003`, `RT-INT-25-004`

## Resolution Table

| Duplicate Trip ID | Workbook Rows | Trip Names Found | Decision | Final Trip ID | Reason |
|---|---:|---|---|---|---|
| `RT-INT-25-002` | 10, 28 | `Serbia & Bosnia`; `Zanzibar 2` | Different Trips | Manual ID assignment required | The same Trip ID is used for two materially different trip names. The workbook has no start date, end date, price, or trip leader evidence to safely decide an automated final ID. |
| `RT-INT-25-003` | 13, 32 | `Oman`; `Lebanon 2` | Different Trips | Manual ID assignment required | The same Trip ID is used for two materially different trip names. The workbook has no start date, end date, price, or trip leader evidence to safely decide an automated final ID. |
| `RT-INT-25-004` | 19, 33 | `Morocco`; `Spain` | Different Trips | Manual ID assignment required | The same Trip ID is used for two materially different trip names. The workbook has no start date, end date, price, or trip leader evidence to safely decide an automated final ID. |

## Evidence

| Trip ID | Workbook Row | Trip Name | Type | Year | Start Date | End Date | Price | Trip Leader |
|---|---:|---|---|---:|---|---|---|---|
| `RT-INT-25-002` | 10 | Serbia & Bosnia | International | 2024 | blank | blank | blank | blank |
| `RT-INT-25-002` | 28 | Zanzibar 2 | International | 2025 | blank | blank | blank | blank |
| `RT-INT-25-003` | 13 | Oman | International | 2024 | blank | blank | blank | blank |
| `RT-INT-25-003` | 32 | Lebanon 2 | International | 2025 | blank | blank | blank | blank |
| `RT-INT-25-004` | 19 | Morocco | International | 2024 | blank | blank | blank | blank |
| `RT-INT-25-004` | 33 | Spain | International | 2025 | blank | blank | blank | blank |

## CTO Decision

These are not safe duplicate merges. They are different trips sharing reused Trip IDs.

Recommended next action:

- Do not import the six duplicate trip rows automatically.
- Ask the business owner to confirm final Trip IDs for each of the six trips.
- After confirmation, create a controlled trip recovery mapping that links old workbook row numbers to final CRM Trip IDs.
- Only after final Trip IDs exist should booking rows referencing these trips be considered for recovery.

