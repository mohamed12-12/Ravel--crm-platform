from __future__ import annotations

import json
import os
import time
import uuid
from contextlib import contextmanager
from dataclasses import fields
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterator

from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine
from sqlalchemy.pool import NullPool

from services.ai_agent.ai_agent_app.agent.agent_state import AgentState
from services.ai_agent.ai_agent_app.agent.session_flow import SessionState
from services.ai_agent.ai_agent_app.config import Settings
from services.ai_agent.ai_agent_app.logger import agent_logger


SESSION_SCHEMA_VERSION = 1
_SESSION_FIELDS = {field.name for field in fields(SessionState)}
_AGENT_STATE_FIELDS = {field.name for field in fields(AgentState)}


class SessionLockBusy(RuntimeError):
    pass


def _utc_now() -> datetime:
    return datetime.now(timezone.utc).replace(microsecond=0)


def _coerce_database_url(raw: str) -> str:
    url = str(raw or "").strip()
    if url.startswith("postgres://"):
        return "postgresql+psycopg2://" + url.removeprefix("postgres://")
    if url.startswith("postgresql://") and "+psycopg2" not in url.split("://", 1)[0]:
        return "postgresql+psycopg2://" + url.removeprefix("postgresql://")
    return url


def _default_sqlite_url() -> str:
    repo_root = Path(__file__).resolve().parents[4]
    raw = os.getenv("RAHMA_SYSTEM_DB_PATH", "").strip()
    if raw:
        path = Path(raw)
        if not path.is_absolute():
            path = repo_root / path
    else:
        path = repo_root / "apps" / "api" / "instance" / "rahma_traveler_dev.db"
    path.parent.mkdir(parents=True, exist_ok=True)
    return f"sqlite:///{path.as_posix()}"


def _session_database_url(settings: Settings) -> str:
    return _coerce_database_url(
        os.getenv("AI_AGENT_SESSION_DATABASE_URL", "").strip()
        or os.getenv("DATABASE_URL", "").strip()
        or _default_sqlite_url()
    )


def _json_dumps(payload: dict[str, Any]) -> str:
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str)


def _json_loads(raw: str | None) -> dict[str, Any]:
    if not raw:
        return {}
    try:
        payload = json.loads(raw)
    except (TypeError, ValueError):
        return {}
    return payload if isinstance(payload, dict) else {}


class DurableSessionStore:
    """Durable, cross-process session snapshots plus a database lease.

    The persisted payload is an explicit JSON projection of SessionState and
    AgentState fields. Runtime-only objects such as providers, tools, locks,
    and services are deliberately not serialized.
    """

    def __init__(self, *, database_url: str, lock_ttl_seconds: float = 30.0, wait_seconds: float = 10.0) -> None:
        self.database_url = _coerce_database_url(database_url)
        self.lock_ttl_seconds = max(float(lock_ttl_seconds), 1.0)
        self.wait_seconds = max(float(wait_seconds), 0.0)
        connect_args = {}
        if self.database_url.startswith("sqlite"):
            connect_args = {"check_same_thread": False, "timeout": max(self.wait_seconds, 5.0)}
        self.engine: Engine = create_engine(self.database_url, poolclass=NullPool, future=True, connect_args=connect_args)
        self._schema_ready = False

    @property
    def is_sqlite(self) -> bool:
        return self.engine.dialect.name == "sqlite"

    # New in this column set: traveler_id/lead_id/raw_phone, added so the CRM
    # can find which conversation belongs to which traveler by a plain SQL
    # query instead of parsing every row's JSON payload. Alembic owns the CRM
    # schema change; ensure_schema() keeps standalone agent deployments and
    # older local SQLite files self-healing.
    _IDENTITY_COLUMNS: dict[str, str] = {
        "traveler_id": "VARCHAR(20)",
        "lead_id": "VARCHAR(50)",
        "raw_phone": "VARCHAR(32)",
    }

    def ensure_schema(self) -> None:
        if self._schema_ready:
            return
        ddl = """
        CREATE TABLE IF NOT EXISTS ai_agent_sessions (
            session_id VARCHAR(64) PRIMARY KEY,
            schema_version INTEGER NOT NULL DEFAULT 1,
            payload TEXT NOT NULL,
            agent_state TEXT,
            version INTEGER NOT NULL DEFAULT 0,
            created_at TIMESTAMP,
            updated_at TIMESTAMP,
            locked_until TIMESTAMP,
            lock_owner VARCHAR(80),
            last_message_key VARCHAR(160),
            traveler_id VARCHAR(20),
            lead_id VARCHAR(50),
            raw_phone VARCHAR(32)
        )
        """
        with self.engine.begin() as connection:
            connection.execute(text(ddl))
            self._ensure_identity_columns(connection)
            if not self.is_sqlite:
                connection.execute(
                    text("CREATE INDEX IF NOT EXISTS ix_ai_agent_sessions_locked_until ON ai_agent_sessions (locked_until)")
                )
            connection.execute(
                text("CREATE INDEX IF NOT EXISTS ix_ai_agent_sessions_traveler_id ON ai_agent_sessions (traveler_id)")
            )
            connection.execute(
                text("CREATE INDEX IF NOT EXISTS ix_ai_agent_sessions_lead_id ON ai_agent_sessions (lead_id)")
            )
        self._schema_ready = True

    def _ensure_identity_columns(self, connection) -> None:
        """Add traveler_id/lead_id/raw_phone to a table created before they
        existed. `CREATE TABLE IF NOT EXISTS` above is a no-op against an
        existing table, so a deployment with rows already in it needs this
        explicit per-column guard -- ALTER TABLE has no portable
        IF-NOT-EXISTS across the sqlite3 versions this also has to run
        against, so the existing columns are checked first instead.
        """
        if self.is_sqlite:
            existing = {
                row[1]
                for row in connection.execute(text("PRAGMA table_info(ai_agent_sessions)")).fetchall()
            }
        else:
            # Filtered to the connection's own search_path (current_schemas),
            # not every schema in the database -- production sets
            # search_path=ravel via DATABASE_URL, and an unfiltered table_name
            # match could otherwise pick up an unrelated same-named table.
            existing = {
                row[0]
                for row in connection.execute(
                    text(
                        "SELECT column_name FROM information_schema.columns "
                        "WHERE table_name = 'ai_agent_sessions' "
                        "AND table_schema = ANY(current_schemas(false))"
                    )
                ).fetchall()
            }
        for column, column_type in self._IDENTITY_COLUMNS.items():
            if column not in existing:
                connection.execute(text(f"ALTER TABLE ai_agent_sessions ADD COLUMN {column} {column_type}"))

    def clear(self) -> None:
        self.ensure_schema()
        with self.engine.begin() as connection:
            connection.execute(text("DELETE FROM ai_agent_sessions"))

    def load(self, session_id: str) -> tuple[SessionState, AgentState, int] | None:
        self.ensure_schema()
        with self.engine.begin() as connection:
            row = connection.execute(
                text("SELECT payload, agent_state, version FROM ai_agent_sessions WHERE session_id = :session_id"),
                {"session_id": session_id},
            ).mappings().first()
        if row is None:
            return None
        session = self._session_from_payload(session_id, _json_loads(row["payload"]))
        agent_state = self._agent_state_from_payload(_json_loads(row["agent_state"]))
        return session, agent_state, int(row["version"] or 0)

    def save(
        self,
        session: SessionState,
        *,
        agent_state: AgentState | None = None,
        expected_version: int | None = None,
        last_message_key: str = "",
    ) -> int:
        self.ensure_schema()
        payload = _json_dumps(self._session_payload(session))
        agent_payload = _json_dumps(self._agent_state_payload(agent_state or AgentState(goal="help the traveler plan a trip")))
        now = _utc_now()
        # NULL rather than "" when unresolved -- an empty string in an indexed
        # column would otherwise group every not-yet-identified session under
        # one matchable value instead of correctly matching nothing.
        identity_params = {
            "traveler_id": str(session.traveler_id or "").strip() or None,
            "lead_id": str(session.lead_id or "").strip() or None,
            "raw_phone": str(session.raw_phone or "").strip() or None,
        }
        with self.engine.begin() as connection:
            existing = connection.execute(
                text("SELECT version FROM ai_agent_sessions WHERE session_id = :session_id"),
                {"session_id": session.id},
            ).mappings().first()
            if existing is None:
                connection.execute(
                    text(
                        """
                        INSERT INTO ai_agent_sessions (
                            session_id, schema_version, payload, agent_state, version,
                            created_at, updated_at, locked_until, lock_owner, last_message_key,
                            traveler_id, lead_id, raw_phone
                        ) VALUES (
                            :session_id, :schema_version, :payload, :agent_state, 1,
                            :created_at, :updated_at, NULL, NULL, :last_message_key,
                            :traveler_id, :lead_id, :raw_phone
                        )
                        """
                    ),
                    {
                        "session_id": session.id,
                        "schema_version": SESSION_SCHEMA_VERSION,
                        "payload": payload,
                        "agent_state": agent_payload,
                        "created_at": now,
                        "updated_at": now,
                        "last_message_key": last_message_key,
                        **identity_params,
                    },
                )
                return 1
            current_version = int(existing["version"] or 0)
            if expected_version is not None and current_version != expected_version:
                raise RuntimeError(
                    f"Session {session.id} version changed from {expected_version} to {current_version} before save."
                )
            new_version = current_version + 1
            connection.execute(
                text(
                    """
                    UPDATE ai_agent_sessions
                    SET payload = :payload,
                        agent_state = :agent_state,
                        schema_version = :schema_version,
                        version = :version,
                        updated_at = :updated_at,
                        last_message_key = :last_message_key,
                        traveler_id = :traveler_id,
                        lead_id = :lead_id,
                        raw_phone = :raw_phone
                    WHERE session_id = :session_id
                    """
                ),
                {
                    "session_id": session.id,
                    "schema_version": SESSION_SCHEMA_VERSION,
                    "payload": payload,
                    "agent_state": agent_payload,
                    "version": new_version,
                    "updated_at": now,
                    "last_message_key": last_message_key,
                    **identity_params,
                },
            )
            return new_version

    @contextmanager
    def session_lock(self, session_id: str) -> Iterator[None]:
        owner = uuid.uuid4().hex
        deadline = time.monotonic() + self.wait_seconds
        while True:
            if self._try_acquire_lock(session_id, owner):
                break
            if time.monotonic() >= deadline:
                raise SessionLockBusy(f"Session {session_id} is already being processed.")
            time.sleep(0.02)
        try:
            yield
        finally:
            self._release_lock(session_id, owner)

    def _try_acquire_lock(self, session_id: str, owner: str) -> bool:
        self.ensure_schema()
        now = _utc_now()
        locked_until = _utc_now() + timedelta(seconds=self.lock_ttl_seconds)
        with self.engine.begin() as connection:
            result = connection.execute(
                text(
                    """
                    UPDATE ai_agent_sessions
                    SET lock_owner = :owner,
                        locked_until = :locked_until
                    WHERE session_id = :session_id
                      AND (
                        lock_owner IS NULL
                        OR locked_until IS NULL
                        OR locked_until < :now
                      )
                    """
                ),
                {"session_id": session_id, "owner": owner, "locked_until": locked_until, "now": now},
            )
            return result.rowcount == 1

    def _release_lock(self, session_id: str, owner: str) -> None:
        try:
            with self.engine.begin() as connection:
                connection.execute(
                    text(
                        """
                        UPDATE ai_agent_sessions
                        SET lock_owner = NULL,
                            locked_until = NULL
                        WHERE session_id = :session_id
                          AND lock_owner = :owner
                        """
                    ),
                    {"session_id": session_id, "owner": owner},
                )
        except Exception:
            agent_logger.warning("Could not release durable session lock session=%s", session_id, exc_info=True)

    @staticmethod
    def _session_payload(session: SessionState) -> dict[str, Any]:
        payload: dict[str, Any] = {}
        for name in sorted(_SESSION_FIELDS):
            payload[name] = getattr(session, name)
        return payload

    @staticmethod
    def _agent_state_payload(agent_state: AgentState) -> dict[str, Any]:
        return {name: getattr(agent_state, name) for name in sorted(_AGENT_STATE_FIELDS)}

    @staticmethod
    def _session_from_payload(session_id: str, payload: dict[str, Any]) -> SessionState:
        # The row's own primary key is the authoritative identity -- a payload
        # that fails to parse (corruption, truncation) must not fabricate an
        # unrelated id, or the next save() silently forks off an orphan row
        # instead of updating the one that was actually requested.
        resolved_id = str(session_id or "").strip() or str(payload.get("id") or "").strip() or uuid.uuid4().hex
        session = SessionState(id=resolved_id)
        for name, value in payload.items():
            if name in _SESSION_FIELDS and name != "id":
                setattr(session, name, value)
        if not isinstance(session.messages, list):
            session.messages = []
        for attr in ("preview", "final_result", "booking_result"):
            value = getattr(session, attr)
            if value is not None and not isinstance(value, dict):
                setattr(session, attr, None)
        if not isinstance(session.room_requirements, dict):
            session.room_requirements = {}
        if not isinstance(session.group_nationality_counts, dict):
            session.group_nationality_counts = {}
        return session

    @staticmethod
    def _agent_state_from_payload(payload: dict[str, Any]) -> AgentState:
        state = AgentState(goal=str(payload.get("goal") or "help the traveler plan a trip"))
        for name, value in payload.items():
            if name in _AGENT_STATE_FIELDS:
                setattr(state, name, value)
        if not isinstance(state.completed_tasks, list):
            state.completed_tasks = []
        if not isinstance(state.pending_tasks, list):
            state.pending_tasks = []
        return state


def build_session_store(settings: Settings) -> DurableSessionStore:
    return DurableSessionStore(
        database_url=_session_database_url(settings),
        lock_ttl_seconds=float(os.getenv("AI_AGENT_SESSION_LOCK_TTL_SECONDS", "30")),
        wait_seconds=float(os.getenv("AI_AGENT_SESSION_LOCK_WAIT_SECONDS", "10")),
    )
