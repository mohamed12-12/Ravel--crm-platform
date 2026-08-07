# CRM Services

Shared CRM business workflows live here.

`system_services/unified_service.py`'s `UnifiedCRMService` is the DB-first
service boundary used by the AI agent (in `shared_service` access mode) and
by CLI/import tooling to coordinate identity resolution, leads, booking
drafts, booking events, trip visibility, and sheet sync. It talks to SQLite
directly (raw `sqlite3`/SQL), including its own idempotent lazy schema
migrations (`ensure_operational_schema()` and friends) for columns that
predate this project's Alembic migration history.

## Relationship to `apps/api/app/services/agent_crm_bridge.py`

`agent_crm_bridge.py`'s `PostgresAgentBridgeService` is the Postgres-native
sibling: same conceptual operations (create traveler/lead/handoff/booking,
resolve identity, search trips), but implemented against the Flask-SQLAlchemy
ORM models in `apps/api/app/models/` instead of raw SQLite SQL. It reuses a
handful of `UnifiedCRMService` static methods directly (phone normalization,
trip-type normalization, lookup-key variants) rather than duplicating them.

Which one actually runs is decided per-request by
`apps/api/app/routes/crm.py`'s `_agent_runtime()`, based on whether the
Flask app's configured database URI is `sqlite:///...` or not -- see
`services/ai_agent/README.md`'s "Dual-backend support" section for how the
AI agent reaches this from the other side (`CRM_ACCESS_MODE=api`).
