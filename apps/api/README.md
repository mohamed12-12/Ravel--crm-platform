# Rahma Traveler Flask CRM

This folder contains the database-backed Flask CRM for Rahma Travel OS. It owns SQLAlchemy models, CRM routes, admin templates, migrations, import scripts, and DB-first workflow services.

For monorepo setup and architecture, start with the root [README](../../README.md) and [docs](../../docs/README.md).

## Setup Instructions

Follow these steps to get the development environment running:

1. **Install Dependencies**
   ```bash
   pip install -r requirements.txt
   ```

2. **Initialize Database**
   ```bash
   flask db upgrade
   ```

3. **Import Excel Data**
   Ensure your Excel file is placed at the path specified in `.env` (default: `data/travelers_database.xlsx`).
   ```bash
   python scripts/import_excel.py
   ```

4. **Run the Application**
   ```bash
   flask run
   ```

## Configuration (.env.example)

Prefer the root `.env.example`. This app also reads `apps/api/.env` for local overrides.

```text
FLASK_ENV=development
SECRET_KEY=
DATABASE_URL=sqlite:///rahma_traveler_dev.db
EXCEL_FILE_PATH=data/travelers_database.xlsx
```

Never commit real secrets or customer data.

## Auth model

Two ways to authenticate as an employee, both checked by `login()` in
`app/routes/auth.py`:

- The single operator identity configured entirely from env vars
  (`ADMIN_USERNAME`/`ADMIN_PASSWORD` or `CRM_ADMIN_PASSWORD_HASH`) -- always
  admin, provisioned/kept in sync in the `users` table on every successful
  login.
- Any employee row created via `/admin/users/create` (`app/routes/admin.py`),
  matched against its own hashed password and using its own stored role
  (`admin`/`manager`/`agent`/`sales` -- see `ROLE_PERMISSIONS` in
  `app/security.py` for what each role can do).

Sessions are cookie-based with a CSRF token (`app/security.py`'s
`generate_csrf_token`/`_csrf_valid`) required on every state-changing
request; `CRM_AUTH_ENABLED=false` disables the whole gate for local/demo use
(see `tests/conftest.py`).

## Data model at a glance

`app/models/`: `Traveler` -- `Lead` -- `TripBooking`/`CEBooking` --
`Interaction` -- `HandoffQueue` -- `TravelerDocument`, all keyed off
`traveler_id`/`lead_id`, plus `User`/`UserAuditLog`/`AssignmentHistory` for
employees. Deleting a `Traveler` (`app/routes/travelers.py`'s `delete()`)
has to explicitly account for every one of those foreign keys before the
row itself can go -- see the inline comments there for the two that are
easy to miss (`TravelerDocument` via an ORM cascade, `BookingStatusHistory`
via an explicit bulk delete).

## Dependencies (requirements.txt)

The project relies on the following core libraries:

- **Flask (3.0.0)**: Web framework.
- **SQLAlchemy (3.1.0)**: ORM for database management.
- **Migrate (4.0.5)**: Database migrations.
- **Login (0.6.3)**: User authentication.
- **SocketIO (5.3.6)**: Real-time WebSocket communication.
- **Pandas (2.1.0) & Openpyxl (3.1.2)**: Excel data processing.
- **Dotenv (1.0.0)**: Environment variable management.
- **Phonenumbers (8.13.0)**: Phone number normalization.

## API Endpoints Summary

| Endpoint | Method | Description |
| :--- | :--- | :--- |
| `/travelers/` | GET | List all travelers with filtering and pagination. |
| `/travelers/` | POST | Create a new traveler record. |
| `/travelers/<id>` | GET | View detailed traveler profile. |
| `/travelers/<id>` | PUT/POST | Update traveler information. |
| `/travelers/<id>` | DELETE | Permanently delete a traveler and every dependent record. |
| `/travelers/export` | GET | Export traveler list to CSV. |
| `/admin/users` | GET | Employee directory (admin only). |
| `/admin/users/create` | POST | Create an employee account. |
| `/admin/users/<id>/update` | POST | Edit an employee's name/email/role. |
| `/admin/users/<id>/toggle` | POST | Activate/deactivate an employee. |
| `/admin/users/<id>/reset-password` | POST | Reset an employee's password. |
| `/admin/users/<id>/delete` | POST | Permanently delete an employee (blocked while they have assigned leads/bookings, or if it's your own account). |
| `/admin/handoffs/` | GET | Kanban board for AI-to-human handoffs. |
| `/admin/handoffs/` | POST | Create a new handoff request (triggers alert). |
| `/admin/handoffs/pending`| GET | Returns the count of pending handoffs. |
| `/api/copy/render` | POST | Deterministic template rendering (Copy Guard). |
| `/api/copy/templates` | GET | List all available message templates. |
| `/api/crm/duplicates` | GET | Identify potential duplicate travelers. |
| `/api/crm/resolve-identity`| POST | Merge duplicate records into a master profile. |
| `/api/crm/agent/read` | POST | AI agent read-only lookups (see `services/ai_agent/README.md`). |
| `/api/crm/agent/write` | POST | AI agent controlled writes (see `services/ai_agent/README.md`). |
