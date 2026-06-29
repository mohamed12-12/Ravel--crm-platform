# Rahma Traveler Backend PRD

## Product Scope

Rahma Traveler is a Flask CRM and AI-assisted travel sales backend. The backend manages travelers, trips, bookings, leads, interactions, AI-to-human handoffs, identity resolution, document uploads, copy templates, and admin health/import views.

## Backend Entry Point

- App factory: `apps/api/app/__init__.py:create_app`
- Local runner: `apps/api/run.py`
- Default local port: `5000`
- Primary database: SQLite via `DATABASE_URL`, defaulting to `apps/api/instance/rahma_traveler_dev.db`

## Core Backend Modules

- Traveler management: `/travelers/`
- Trip inventory: `/trips/`
- Booking management: `/bookings/`
- Lead management: `/leads/`
- Interaction logs: `/interactions/`
- Handoff queue: `/admin/handoffs/`
- Admin dashboard, import, duplicates, sync issues, DB health: `/admin/*`
- CRM API identity endpoints: `/api/crm/*`
- Copy rendering endpoints: `/api/copy/*`

## Critical Business Rules

- Traveler IDs must be generated from the operational database max ID.
- Duplicate travelers are detected by shared `phone_lookup_key`.
- Blacklisted travelers must block unsafe lead/traveler creation.
- Booking drafts must preserve traveler, trip, room, payment, passport, and group-size fields.
- International bookings require passport awareness.
- Passport uploads must be limited to allowed image/PDF types and safe upload paths.
- Handoff queue items must move through `Pending`, `In Progress`, and `Resolved`.
- Trip inventory filters must preserve query state across detail navigation.
- Booking search must perform partial case-insensitive matching across booking ID, traveler, trip, notes, source, and traveler ID.

## Test Data Requirements

- At least one duplicate traveler pair with the same `phone_lookup_key`.
- At least one pending handoff request.
- At least one open trip and one closed international trip.
- At least one booking with searchable notes such as `Test Booking`.
- At least one traveler with a passport document row and one uploaded passport file when testing document view.

## Security Requirements

- Unsafe file upload names and path traversal must be rejected.
- Document view routes must serve only files belonging to the requested traveler.
- Admin/write routes should not expose unvalidated mutation behavior.
- Production config must require secrets and disable debug behavior.
- API and browser mutation routes should return consistent status codes and errors.

## Expected Backend Test Areas

- Traveler create, update, search, export, duplicate phone protection, document upload, document view.
- Trip list filters, trip detail, create/update trip, capacity fields, commercial notes.
- Booking list search, create draft, status transitions, payment updates, history/event trail.
- Lead create/update/search and handoff creation from lead detail.
- Handoff queue create, pending count, assign/update, resolve.
- Identity duplicate scan and merge behavior.
- Copy API render and template listing.
- Admin DB health and import/sync issue routes.
