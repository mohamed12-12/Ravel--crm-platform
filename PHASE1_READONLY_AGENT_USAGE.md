# Phase 1 Read-Only Agent Usage

Date: 2026-05-11

## Purpose

This prototype reads the prepared workbook and simulates the first sales-agent decision flow without changing CRM data.

It can:

- normalize the incoming phone number
- search by `Phone Lookup Key`
- search by `Integrated WhatsApp`
- detect `single_match`, `multiple_matches`, and `not_found`
- apply handoff rules for blocked or risky customers
- return `Open` trips
- return `Date TBD` trips as unconfirmed follow-up options

It does not:

- create travelers
- update the workbook
- create bookings
- write interactions

## Command

```powershell
python scripts\phase1_readonly_agent.py --workbook "RT - Travelers Database.phase0.ready.xlsx" --name "Customer Name" --phone "01005828000" --country-code "20" --trip-type "local"
```

## Inputs

- `--workbook`: path to the prepared workbook
- `--name`: customer name from DM
- `--phone`: raw phone or WhatsApp number
- `--country-code`: optional country code
- `--trip-type`: optional `local` or `international`

## Output Shape

The script prints JSON with:

- `lookup_phone`
- `match_status`
- `traveler`
- `handoff_required`
- `handoff_reason`
- `actions`
- `trip_result`

## Main Behaviors

- `single_match`: found one traveler
- `multiple_matches`: duplicate phone, human handoff required
- `not_found`: new customer path, collect data only
- `blacklisted_customer`: stop sales flow
- `show_open_trips`: safe to recommend
- `offer_date_tbd_follow_up`: no confirmed trip dates, but future trip exists without dates
