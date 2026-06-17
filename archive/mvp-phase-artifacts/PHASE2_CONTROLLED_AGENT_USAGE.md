# Phase 2 Controlled Agent Usage

Date: 2026-05-11

## Purpose

This prototype extends Phase 1 with controlled workbook writes.

It can:

- run the Phase 1 lookup and trip logic
- create a new traveler when no CRM match exists
- write only approved raw traveler fields
- preserve formula columns in `Travelers`
- log each interaction into `Interactions`
- block duplicate-phone auto-creation

It does not:

- create bookings
- take payments
- update `Trip Bookings`
- auto-resolve duplicate traveler rows

## Command

```powershell
python scripts\phase2_controlled_agent.py --workbook "RT - Travelers Database.phase0.ready.xlsx" --output "RT - Travelers Database.phase2.prototype.xlsx" --name "Customer Name" --phone "01012345678" --trip-type "local" --channel "instagram" --source "DM"
```

## Inputs

- `--workbook`: source workbook
- `--output`: output workbook path
- `--name`: customer name
- `--phone`: raw phone or WhatsApp number
- `--country-code`: optional country code
- `--trip-type`: `local` or `international`
- `--channel`: message channel
- `--source`: lead source label
- `--agent-notes`: optional internal note

## Write Rules

- `single_match`: no traveler creation, interaction only
- `multiple_matches`: no traveler creation, interaction + handoff log
- `not_found`: create traveler + interaction log

## Main Written Fields

In `Travelers`:

- `Traveler ID`
- `Full Name`
- `Code`
- `WhatsApp`
- `Integrated WhatsApp`
- `Normalized WhatsApp`
- `Phone Lookup Key`
- `Lead Source`
- `Created At`
- `Last Contacted At`
- `Agent Notes`
- `Data Audit`

In `Interactions`:

- one new interaction row per run
- suggested trip IDs
- handoff flag and reason
- action summary
