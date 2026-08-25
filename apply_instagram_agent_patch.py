#!/usr/bin/env python3
"""Patch: wire Instagram webhook to the real ToolCallingSessionRuntime agent
instead of the static build_instagram_reply() stub.

Run from repo root: python3 apply_instagram_agent_patch.py
Then review with: git diff
"""
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent

def patch_file(path: Path, replacements):
    text = path.read_text()
    for old, new in replacements:
        if old not in text:
            print(f"FAILED: expected block not found in {path}")
            print("---- expected ----")
            print(old)
            sys.exit(1)
        if text.count(old) > 1:
            print(f"FAILED: expected block is not unique in {path} (found {text.count(old)}x)")
            sys.exit(1)
        text = text.replace(old, new, 1)
    path.write_text(text)
    print(f"OK: patched {path}")


# ---------------------------------------------------------------------------
# 1. session_store.py -- add instagram_sender_id identity column + lookup
# ---------------------------------------------------------------------------
session_store = REPO_ROOT / "services/ai_agent/ai_agent_app/agent/session_store.py"

patch_file(session_store, [
    (
        '    _IDENTITY_COLUMNS: dict[str, str] = {\n'
        '        "traveler_id": "VARCHAR(20)",\n'
        '        "lead_id": "VARCHAR(50)",\n'
        '        "raw_phone": "VARCHAR(32)",\n'
        '    }',
        '    _IDENTITY_COLUMNS: dict[str, str] = {\n'
        '        "traveler_id": "VARCHAR(20)",\n'
        '        "lead_id": "VARCHAR(50)",\n'
        '        "raw_phone": "VARCHAR(32)",\n'
        '        "instagram_sender_id": "VARCHAR(64)",\n'
        '    }',
    ),
    (
        '            connection.execute(\n'
        '                text("CREATE INDEX IF NOT EXISTS ix_ai_agent_sessions_lead_id ON ai_agent_sessions (lead_id)")\n'
        '            )\n'
        '        self._schema_ready = True',
        '            connection.execute(\n'
        '                text("CREATE INDEX IF NOT EXISTS ix_ai_agent_sessions_lead_id ON ai_agent_sessions (lead_id)")\n'
        '            )\n'
        '            connection.execute(\n'
        '                text(\n'
        '                    "CREATE INDEX IF NOT EXISTS ix_ai_agent_sessions_instagram_sender_id "\n'
        '                    "ON ai_agent_sessions (instagram_sender_id)"\n'
        '                )\n'
        '            )\n'
        '        self._schema_ready = True',
    ),
    (
        '        identity_params = {\n'
        '            "traveler_id": str(session.traveler_id or "").strip() or None,\n'
        '            "lead_id": str(session.lead_id or "").strip() or None,\n'
        '            "raw_phone": str(session.raw_phone or "").strip() or None,\n'
        '        }',
        '        identity_params = {\n'
        '            "traveler_id": str(session.traveler_id or "").strip() or None,\n'
        '            "lead_id": str(session.lead_id or "").strip() or None,\n'
        '            "raw_phone": str(session.raw_phone or "").strip() or None,\n'
        '            "instagram_sender_id": str(getattr(session, "instagram_sender_id", "") or "").strip() or None,\n'
        '        }',
    ),
    (
        '                        INSERT INTO ai_agent_sessions (\n'
        '                            session_id, schema_version, payload, agent_state, version,\n'
        '                            created_at, updated_at, locked_until, lock_owner, last_message_key,\n'
        '                            traveler_id, lead_id, raw_phone\n'
        '                        ) VALUES (\n'
        '                            :session_id, :schema_version, :payload, :agent_state, 1,\n'
        '                            :created_at, :updated_at, NULL, NULL, :last_message_key,\n'
        '                            :traveler_id, :lead_id, :raw_phone\n'
        '                        )',
        '                        INSERT INTO ai_agent_sessions (\n'
        '                            session_id, schema_version, payload, agent_state, version,\n'
        '                            created_at, updated_at, locked_until, lock_owner, last_message_key,\n'
        '                            traveler_id, lead_id, raw_phone, instagram_sender_id\n'
        '                        ) VALUES (\n'
        '                            :session_id, :schema_version, :payload, :agent_state, 1,\n'
        '                            :created_at, :updated_at, NULL, NULL, :last_message_key,\n'
        '                            :traveler_id, :lead_id, :raw_phone, :instagram_sender_id\n'
        '                        )',
    ),
    (
        '                        traveler_id = :traveler_id,\n'
        '                        lead_id = :lead_id,\n'
        '                        raw_phone = :raw_phone\n'
        '                    WHERE session_id = :session_id',
        '                        traveler_id = :traveler_id,\n'
        '                        lead_id = :lead_id,\n'
        '                        raw_phone = :raw_phone,\n'
        '                        instagram_sender_id = :instagram_sender_id\n'
        '                    WHERE session_id = :session_id',
    ),
    (
        '    def clear(self) -> None:\n'
        '        self.ensure_schema()\n'
        '        with self.engine.begin() as connection:\n'
        '            connection.execute(text("DELETE FROM ai_agent_sessions"))',
        '    def clear(self) -> None:\n'
        '        self.ensure_schema()\n'
        '        with self.engine.begin() as connection:\n'
        '            connection.execute(text("DELETE FROM ai_agent_sessions"))\n'
        '\n'
        '    def find_session_id_by_instagram_sender_id(self, sender_id: str):\n'
        '        sender_id = str(sender_id or "").strip()\n'
        '        if not sender_id:\n'
        '            return None\n'
        '        self.ensure_schema()\n'
        '        with self.engine.begin() as connection:\n'
        '            row = connection.execute(\n'
        '                text("SELECT session_id FROM ai_agent_sessions WHERE instagram_sender_id = :sid"),\n'
        '                {"sid": sender_id},\n'
        '            ).mappings().first()\n'
        '        return str(row["session_id"]) if row else None',
    ),
])

# ---------------------------------------------------------------------------
# 2. session_flow.py -- add instagram_sender_id field to SessionState
# ---------------------------------------------------------------------------
session_flow = REPO_ROOT / "services/ai_agent/ai_agent_app/agent/session_flow.py"

patch_file(session_flow, [
    (
        '    raw_phone: str = ""',
        '    raw_phone: str = ""\n'
        '    instagram_sender_id: str = ""',
    ),
])

# ---------------------------------------------------------------------------
# 3. tool_calling_runtime.py -- get-or-create session for an Instagram sender
# ---------------------------------------------------------------------------
tool_calling_runtime = REPO_ROOT / "services/ai_agent/ai_agent_app/agent/tool_calling_runtime.py"

patch_file(tool_calling_runtime, [
    (
        '        self._sessions[session.id] = session\n'
        '        self._persist_session(session)\n'
        '        return session\n'
        '\n'
        '    def handle_message_by_id(self, session_id: str, text: str, gateway) -> SessionState | None:',
        '        self._sessions[session.id] = session\n'
        '        self._persist_session(session)\n'
        '        return session\n'
        '\n'
        '    def get_or_create_session_for_instagram(self, sender_id: str, gateway=None) -> SessionState:\n'
        '        """Get-or-create a durable session keyed by Instagram sender id.\n'
        '\n'
        '        Mirrors the raw_phone/traveler_id identity pattern in session_store.py:\n'
        '        an Instagram sender is looked up by their scoped sender_id instead of\n'
        '        a browser session cookie, and reused across webhook deliveries.\n'
        '        """\n'
        '        sender_id = str(sender_id or "").strip()\n'
        '        existing_id = self._session_store.find_session_id_by_instagram_sender_id(sender_id)\n'
        '        if existing_id:\n'
        '            loaded = self._session_store.load(existing_id)\n'
        '            if loaded is not None:\n'
        '                session, agent_state, _ = loaded\n'
        '                self._sessions[existing_id] = session\n'
        '                self._state_by_session[existing_id] = agent_state\n'
        '                return session\n'
        '        session = self.create_session(gateway)\n'
        '        session.instagram_sender_id = sender_id\n'
        '        self._persist_session(session)\n'
        '        return session\n'
        '\n'
        '    def handle_message_by_id(self, session_id: str, text: str, gateway) -> SessionState | None:',
    ),
])

# ---------------------------------------------------------------------------
# 4. server.py -- route the Instagram webhook through the real agent
# ---------------------------------------------------------------------------
server = REPO_ROOT / "services/ai_agent/ai_agent_app/server.py"

patch_file(server, [
    (
        '                    if result.get("created"):\n'
        '                        persisted += 1\n'
        '                        draft = build_instagram_reply(event)\n'
        '                        generated_replies.append({\n'
        '                            "recipient_id": event.sender_id,\n'
        '                            "text": draft.text,\n'
        '                            "reason": draft.reason\n'
        '                        })',
        '                    if result.get("created"):\n'
        '                        persisted += 1\n'
        '                        session_runtime = app.config["SESSIONS"]\n'
        '                        if hasattr(session_runtime, "get_or_create_session_for_instagram"):\n'
        '                            sheet_gateway = app.config.get("SHEET_GATEWAY")\n'
        '                            ig_session = session_runtime.get_or_create_session_for_instagram(\n'
        '                                event.sender_id, gateway=sheet_gateway\n'
        '                            )\n'
        '                            agent_result = session_runtime.handle_message_by_id(\n'
        '                                ig_session.id, event.text or "", sheet_gateway\n'
        '                            )\n'
        '                            if agent_result is not None and agent_result.messages:\n'
        '                                reply_text = agent_result.messages[-1].get("text", "")\n'
        '                            else:\n'
        '                                reply_text = ""\n'
        '                            draft = SimpleNamespace(text=reply_text, reason="tool_calling_agent")\n'
        '                        else:\n'
        '                            draft = build_instagram_reply(event)\n'
        '                        generated_replies.append({\n'
        '                            "recipient_id": event.sender_id,\n'
        '                            "text": draft.text,\n'
        '                            "reason": draft.reason\n'
        '                        })',
    ),
])

print("\nAll patches applied. Review with: git diff")
print("NOTE: confirm gateway should be app.config['SHEET_GATEWAY'] before deploying")
print("this was inferred, not confirmed from the actual web widget chat route.")
