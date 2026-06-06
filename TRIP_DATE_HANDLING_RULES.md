# Trip Date Handling Rules

Date: 2026-05-11

## Goal

When the sales agent reads the `Trips` sheet and finds missing or invalid `Start Date` / `End Date`, it must respond safely without offering outdated or unconfirmed trips as if they are available.

## Rules

### 1. Fully dated future trip

Condition:

- `Start Date` exists and is a valid date
- `End Date` exists and is a valid date
- `Start Date >= today`

Agent behavior:

- The trip can be recommended normally.

Sales status:

- `Open`

### 2. Fully dated past trip

Condition:

- `End Date` exists and is before today

Agent behavior:

- Do not recommend.

Sales status:

- `Closed`

### 3. Missing both dates, current or future year

Condition:

- `Start Date` is empty
- `End Date` is empty
- `Year >= current year`

Agent behavior:

- Do not present as confirmed availability.
- The agent may say:
  `We have this trip in our plan, but the travel dates are not confirmed yet. I can note your interest and a team member can follow up once dates are set.`

Sales status:

- `Date TBD`

### 4. Missing both dates, old year

Condition:

- `Start Date` is empty
- `End Date` is empty
- `Year < current year`

Agent behavior:

- Do not recommend.
- Treat as archive/history, not an active trip.

Sales status:

- `Archived`

### 5. Invalid date text or partial date data

Condition:

- One date exists but is invalid text
- Or one date exists and the other is missing
- Or date format cannot be parsed safely

Agent behavior:

- Do not recommend as available.
- The agent may say:
  `I can see this trip in our system, but the travel dates need confirmation from the team first.`

Sales status:

- `Date Fix Needed`

## Agent Filtering Order

When a customer asks about trips:

1. Filter by `Type` (`Local` or `International`)
2. Prefer rows with `Sales Status = Open`
3. If no `Open` trips exist:
   - optionally mention `Date TBD` trips as unconfirmed, if the business wants that
4. Never recommend:
   - `Closed`
   - `Archived`
   - `Date Fix Needed`

## Recommended Customer Response Priority

1. Show confirmed upcoming trips first
2. If none exist, mention unconfirmed `Date TBD` trips
3. If neither exists, say there are no confirmed trips currently and offer follow-up

## Internal Note

This rule keeps the agent useful even when the trip sheet is incomplete, while still protecting the business from advertising old or broken trip records as active inventory.
