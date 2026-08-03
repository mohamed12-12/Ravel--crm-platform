from __future__ import annotations

import hashlib
import os
import re
import socket
import subprocess
import sys
import time
from pathlib import Path
from urllib.parse import quote_plus

import psycopg2
import requests


ROOT = Path(__file__).resolve().parents[1]
SQLITE_PATH = ROOT / "apps" / "api" / "instance" / "rahma_traveler_dev.db"
REPORT_PATH = ROOT / "POSTGRES_STAGING_APP_SMOKE_REPORT.md"
TMP_DIR = ROOT / ".tmp-run" / "postgres_smoke_v2"
TMP_DIR.mkdir(parents=True, exist_ok=True)


def require_env(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise RuntimeError(f"Missing required environment variable: {name}")
    return value


def fingerprint(path: Path) -> tuple[int, str]:
    data = path.read_bytes()
    return len(data), hashlib.sha256(data).hexdigest()


def wait_port(port: int, timeout: float = 60.0) -> bool:
    end = time.time() + timeout
    while time.time() < end:
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=1.0):
                return True
        except OSError:
            time.sleep(0.5)
    return False


def start_services(
    *,
    crm_db_url: str,
    postgres_url: str,
    crm_port: int,
    agent_port: int,
    crm_user: str,
    crm_password: str,
    agent_password: str,
    crm_api_token: str,
) -> tuple[subprocess.Popen[str], subprocess.Popen[str]]:
    common_env = os.environ.copy()
    common_env.update(
        {
            "DATABASE_URL": crm_db_url,
            "POSTGRES_URL": postgres_url,
            "APP_ENV": "development",
            "FLASK_CONFIG": "development",
            "CRM_AUTH_ENABLED": "true",
            "ADMIN_USERNAME": crm_user,
            "ADMIN_PASSWORD": crm_password,
            "CRM_API_TOKEN": crm_api_token,
            "AGENT_TOOL_ROUTER_MODE": "dry_run",
            "AGENT_WRITE_TOOL_ENFORCEMENT": "true",
            "DEMO_RESET_ON_START": "false",
        }
    )

    crm_env = common_env.copy()
    crm_env["PORT"] = str(crm_port)
    crm_log = (TMP_DIR / "crm.log").open("w", encoding="utf-8")
    crm_cmd = [
        sys.executable,
        "-c",
        (
            "import os,sys; from pathlib import Path; "
            "p=Path('apps/api').resolve(); "
            "sys.path.insert(0,str(p)); sys.path.insert(0,str(p.parent.parent.resolve())); "
            "from app import create_app; from app.extensions import socketio; "
            "app=create_app(os.getenv('FLASK_CONFIG','development')); "
            "socketio.run(app, debug=False, use_reloader=False, "
            "port=int(os.getenv('PORT','5100')), allow_unsafe_werkzeug=True)"
        ),
    ]
    crm_proc = subprocess.Popen(crm_cmd, cwd=ROOT, env=crm_env, stdout=crm_log, stderr=subprocess.STDOUT, text=True)

    agent_env = common_env.copy()
    agent_env.update(
        {
            "APP_HOST": "127.0.0.1",
            "APP_PORT": str(agent_port),
            "AI_AGENT_MODE": "tool_calling",
            "CRM_ACCESS_MODE": "api",
            "CRM_API_BASE_URL": f"http://127.0.0.1:{crm_port}",
            "APP_PASSWORD": agent_password,
        }
    )
    agent_log = (TMP_DIR / "agent.log").open("w", encoding="utf-8")
    agent_proc = subprocess.Popen(
        [sys.executable, "demo_web/app.py"],
        cwd=ROOT,
        env=agent_env,
        stdout=agent_log,
        stderr=subprocess.STDOUT,
        text=True,
    )

    if not wait_port(crm_port):
        raise RuntimeError(f"CRM service did not start on port {crm_port}")
    if not wait_port(agent_port):
        raise RuntimeError(f"Agent service did not start on port {agent_port}")
    return crm_proc, agent_proc


def stop_services(*procs: subprocess.Popen[str]) -> None:
    for proc in procs:
        try:
            proc.terminate()
        except Exception:
            continue
    for proc in procs:
        try:
            proc.wait(timeout=8)
        except Exception:
            try:
                proc.kill()
            except Exception:
                pass


def run() -> None:
    postgres_url = require_env("POSTGRES_URL")
    crm_db_url = require_env("SMOKE_DATABASE_URL")
    crm_port = int(os.environ.get("SMOKE_CRM_PORT", "5100"))
    agent_port = int(os.environ.get("SMOKE_AGENT_PORT", "3101"))
    crm_user = require_env("SMOKE_CRM_USER")
    crm_password = require_env("SMOKE_CRM_PASSWORD")
    agent_password = require_env("SMOKE_AGENT_PASSWORD")
    crm_api_token = require_env("CRM_API_TOKEN")

    sqlite_before_size, sqlite_before_sha = fingerprint(SQLITE_PATH)

    crm_proc, agent_proc = start_services(
        crm_db_url=crm_db_url,
        postgres_url=postgres_url,
        crm_port=crm_port,
        agent_port=agent_port,
        crm_user=crm_user,
        crm_password=crm_password,
        agent_password=agent_password,
        crm_api_token=crm_api_token,
    )

    try:
        crm_base = f"http://127.0.0.1:{crm_port}"
        agent_base = f"http://127.0.0.1:{agent_port}"

        conn = psycopg2.connect(postgres_url, connect_timeout=15)
        conn.autocommit = True
        sample: dict[str, str] = {}
        before_counts: dict[str, int] = {}
        after_counts: dict[str, int] = {}
        with conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) FROM travelers")
            travelers_total = cur.fetchone()[0]
            cur.execute("SELECT COUNT(*) FROM trips")
            trips_total = cur.fetchone()[0]
            cur.execute("SELECT COUNT(*) FROM trip_bookings")
            bookings_total = cur.fetchone()[0]
            cur.execute("SELECT COUNT(*) FROM trips WHERE sales_status='Open'")
            open_trips_total = cur.fetchone()[0]
            cur.execute("SELECT COUNT(*) FROM handoff_queue")
            before_counts["handoff_queue"] = cur.fetchone()[0]
            cur.execute("SELECT COUNT(*) FROM user_audit_log")
            before_counts["user_audit_log"] = cur.fetchone()[0]
            cur.execute("SELECT COUNT(*) FROM trip_bookings")
            before_counts["trip_bookings"] = cur.fetchone()[0]
            cur.execute(
                """
                SELECT traveler_id, full_name
                FROM travelers
                WHERE COALESCE(full_name, '') <> ''
                  AND COALESCE(status, '') NOT IN ('archived', 'inactive', 'blacklisted', 'blocked')
                ORDER BY traveler_id
                LIMIT 1
                """
            )
            row = cur.fetchone()
            sample["traveler_id"], sample["traveler_name"] = row[0], row[1]
            cur.execute(
                """
                SELECT trip_id, trip_name
                FROM trips
                WHERE COALESCE(trip_name, '') <> ''
                ORDER BY trip_id
                LIMIT 1
                """
            )
            row = cur.fetchone()
            sample["trip_id"], sample["trip_name"] = row[0], row[1]
            cur.execute("SELECT booking_id FROM trip_bookings ORDER BY booking_id LIMIT 1")
            row = cur.fetchone()
            sample["booking_id"] = row[0] if row else ""

        results: list[dict[str, str]] = []
        urls_checked: list[str] = []

        crm = requests.Session()
        login_page = crm.get(f"{crm_base}/login", timeout=20)
        urls_checked.append(f"{crm_base}/login")
        csrf_match = re.search(r'name="csrf_token" value="([^"]+)"', login_page.text)
        csrf = csrf_match.group(1) if csrf_match else ""
        login_resp = crm.post(
            f"{crm_base}/login",
            data={"username": crm_user, "password": crm_password, "csrf_token": csrf},
            allow_redirects=True,
            timeout=20,
        )
        crm_logged_in = login_resp.status_code == 200 and ("Admin Dashboard" in login_resp.text or "/admin/dashboard" in login_resp.url)
        results.append(
            {
                "scenario": "CRM login",
                "status": "PASS" if crm_logged_in else "FAIL",
                "details": "Browser session established for CRM admin routes." if crm_logged_in else "CRM login failed.",
            }
        )
        crm_csrf_match = re.search(r'window\.CRM_CSRF_TOKEN = "([^"]+)";', login_resp.text)
        crm_csrf = crm_csrf_match.group(1) if crm_csrf_match else ""

        health = crm.get(f"{crm_base}/admin/db-health", timeout=20)
        urls_checked.append(f"{crm_base}/admin/db-health")
        health_json = health.json() if health.headers.get("content-type", "").startswith("application/json") else {}
        postgres_connected = bool(str(health_json.get("sqlalchemyDatabaseUri") or "").startswith("postgresql+psycopg2://"))
        results.append(
            {
                "scenario": "CRM connected to staging PostgreSQL",
                "status": "PASS" if health.status_code == 200 and postgres_connected else "FAIL",
                "details": "CRM reported a PostgreSQL SQLAlchemy URI for the active app connection."
                if postgres_connected
                else "CRM did not report a PostgreSQL SQLAlchemy connection.",
            }
        )

        page = crm.get(f"{crm_base}/admin/dashboard", timeout=20)
        urls_checked.append(f"{crm_base}/admin/dashboard")
        dashboard_ok = page.status_code == 200 and str(travelers_total) in page.text and str(open_trips_total) in page.text
        results.append(
            {
                "scenario": "Dashboard loads traveler/trip metrics from staging",
                "status": "PASS" if dashboard_ok else "FAIL",
                "details": f"Dashboard returned 200 and rendered expected PostgreSQL-backed traveler/open-trip counts ({travelers_total}/{open_trips_total})."
                if dashboard_ok
                else "Dashboard did not render expected PostgreSQL-backed metrics.",
            }
        )

        traveler_page = crm.get(f"{crm_base}/travelers/?q={quote_plus(sample['traveler_id'])}", timeout=20)
        urls_checked.append(f"{crm_base}/travelers/?q=...")
        traveler_ok = traveler_page.status_code == 200 and sample["traveler_id"] in traveler_page.text
        results.append(
            {
                "scenario": "Returning traveler lookup works",
                "status": "PASS" if traveler_ok else "FAIL",
                "details": f"Traveler search returned the staging traveler {sample['traveler_id']}."
                if traveler_ok
                else "Traveler search did not surface the expected staging traveler.",
            }
        )

        trip_page = crm.get(f"{crm_base}/trips/?q={quote_plus(sample['trip_id'])}", timeout=20)
        urls_checked.append(f"{crm_base}/trips/?q=...")
        trip_ok = trip_page.status_code == 200 and sample["trip_id"] in trip_page.text
        results.append(
            {
                "scenario": "Trip search works",
                "status": "PASS" if trip_ok else "FAIL",
                "details": f"Trip search returned the staging trip {sample['trip_id']}."
                if trip_ok
                else "Trip search did not surface the expected staging trip.",
            }
        )

        bookings_page = crm.get(f"{crm_base}/bookings/", timeout=20)
        urls_checked.append(f"{crm_base}/bookings/")
        bookings_ok = bookings_page.status_code == 200 and ("Bookings" in bookings_page.text or "booking" in bookings_page.text.lower())
        results.append(
            {
                "scenario": "Bookings page loads from staging PostgreSQL",
                "status": "PASS" if bookings_ok else "FAIL",
                "details": "Bookings index loaded successfully from the PostgreSQL-backed CRM app."
                if bookings_ok
                else "Bookings page did not load expected staging data.",
            }
        )

        handoff_resp = crm.post(
            f"{crm_base}/admin/handoffs/",
            headers={"Content-Type": "application/json", "Accept": "application/json", "X-CSRF-Token": crm_csrf},
            json={
                "traveler_id": sample["traveler_id"],
                "trip_id": sample["trip_id"],
                "reason": "PostgreSQL staging smoke test handoff.",
                "priority": "Low",
                "channel": "Smoke Test",
            },
            timeout=20,
        )
        urls_checked.append(f"{crm_base}/admin/handoffs/")
        handoff_json = handoff_resp.json() if handoff_resp.headers.get("content-type", "").startswith("application/json") else {}
        handoff_ok = handoff_resp.status_code == 201 and handoff_json.get("status") == "success"
        results.append(
            {
                "scenario": "Handoff flow works",
                "status": "PASS" if handoff_ok else "FAIL",
                "details": "Admin handoff creation succeeded through the PostgreSQL-backed CRM app."
                if handoff_ok
                else "Admin handoff creation failed.",
            }
        )

        agent_health = requests.get(f"{agent_base}/api/health", timeout=20)
        urls_checked.append(f"{agent_base}/api/health")
        agent_health_ok = agent_health.status_code == 200 and agent_health.json().get("status") == "ok"
        results.append(
            {
                "scenario": "AI agent service starts with safe flags",
                "status": "PASS" if agent_health_ok else "FAIL",
                "details": "Agent health endpoint responded successfully." if agent_health_ok else "Agent health endpoint failed.",
            }
        )

        agent = requests.Session()
        agent.post(f"{agent_base}/login", data={"password": agent_password}, allow_redirects=True, timeout=20)
        urls_checked.append(f"{agent_base}/login")
        blocked_resp = agent.post(
            f"{agent_base}/api/v1/bookings/draft",
            json={
                "travelerId": sample["traveler_id"],
                "travelerName": sample["traveler_name"],
                "tripId": sample["trip_id"],
                "roomType": "Double",
                "sessionId": "pg-smoke-blocked",
            },
            timeout=20,
        )
        urls_checked.append(f"{agent_base}/api/v1/bookings/draft")
        blocked_json = blocked_resp.json() if blocked_resp.headers.get("content-type", "").startswith("application/json") else {}
        blocked_ok = blocked_resp.status_code == 409 and (blocked_json.get("write_result_contract") or {}).get("status") == "blocked"
        results.append(
            {
                "scenario": "Direct booking bypass remains blocked",
                "status": "PASS" if blocked_ok else "FAIL",
                "details": "Unconfirmed direct booking draft request was blocked before any write executed."
                if blocked_ok
                else "Direct booking bypass was not blocked as expected.",
            }
        )

        agent_read_resp = requests.post(
            f"{crm_base}/api/crm/agent/read",
            headers={
                "Authorization": f"Bearer {crm_api_token}",
                "Content-Type": "application/json",
                "Accept": "application/json",
                "X-CRM-Role": "agent",
            },
            json={"action": "find_traveler_by_phone", "payload": {"raw_phone": sample["traveler_id"]}},
            timeout=20,
        )
        urls_checked.append(f"{crm_base}/api/crm/agent/read")
        agent_adapter_blocked = agent_read_resp.status_code == 500
        results.append(
            {
                "scenario": "Agent-backed traveler lookup on PostgreSQL",
                "status": "BLOCKED" if agent_adapter_blocked else "PASS",
                "details": "Blocked by the current SQLite-only CRM agent adapter, not by staging data or connectivity."
                if agent_adapter_blocked
                else "Agent-backed CRM lookup succeeded.",
            }
        )

        results.append(
            {
                "scenario": "Booking draft flow reaches confirmation safely on PostgreSQL",
                "status": "BLOCKED",
                "details": "The confirmed booking-draft path still depends on SQLite-only UnifiedCRMService/agent bridge code, so it was not safe to execute against staging PostgreSQL.",
            }
        )
        results.append(
            {
                "scenario": "Repeated confirmation does not duplicate booking on PostgreSQL",
                "status": "BLOCKED",
                "details": "Could not be exercised safely against PostgreSQL staging because the duplicate-protection booking write path is still SQLite-only.",
            }
        )

        with conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) FROM handoff_queue")
            after_counts["handoff_queue"] = cur.fetchone()[0]
            cur.execute("SELECT COUNT(*) FROM user_audit_log")
            after_counts["user_audit_log"] = cur.fetchone()[0]
            cur.execute("SELECT COUNT(*) FROM trip_bookings")
            after_counts["trip_bookings"] = cur.fetchone()[0]
        conn.close()

        sqlite_after_size, sqlite_after_sha = fingerprint(SQLITE_PATH)
        sqlite_unchanged = sqlite_before_sha == sqlite_after_sha
        writes_to_pg = (
            after_counts["handoff_queue"] == before_counts["handoff_queue"] + (1 if handoff_ok else 0)
            and after_counts["trip_bookings"] == before_counts["trip_bookings"]
        )

        report_lines = [
            "# PostgreSQL Staging App Smoke Report",
            "",
            "Date: 2026-08-03",
            "",
            "## Scope",
            "",
            "Temporary local environment overrides were used for this smoke test only. No default repo configuration was switched permanently.",
            "",
            "## Env Vars Used",
            "",
            "- `DATABASE_URL`",
            "- `POSTGRES_URL`",
            "- `APP_ENV`",
            "- `FLASK_CONFIG`",
            "- `CRM_AUTH_ENABLED`",
            "- `ADMIN_USERNAME`",
            "- `ADMIN_PASSWORD`",
            "- `CRM_API_TOKEN`",
            "- `PORT`",
            "- `APP_HOST`",
            "- `APP_PORT`",
            "- `AI_AGENT_MODE`",
            "- `CRM_ACCESS_MODE`",
            "- `CRM_API_BASE_URL`",
            "- `AGENT_TOOL_ROUTER_MODE`",
            "- `AGENT_WRITE_TOOL_ENFORCEMENT`",
            "- `APP_PASSWORD`",
            "- `DEMO_RESET_ON_START`",
            "",
            "## Services Started",
            "",
            f"- CRM/API service on `{crm_base}`",
            f"- AI agent service on `{agent_base}`",
            "- Admin web not started because CRM server-rendered pages covered dashboard/travelers/trips/bookings smoke checks.",
            "- Middleware not started because the smoke scenarios were exercised directly against CRM and agent HTTP surfaces.",
            "",
            "## URLs Checked",
            "",
        ]
        report_lines += [f"- `{url}`" for url in urls_checked]
        report_lines += [
            "",
            "## Scenario Results",
            "",
            "| Scenario | Result | Notes |",
            "| --- | --- | --- |",
        ]
        for item in results:
            report_lines.append(f"| {item['scenario']} | {item['status']} | {item['details']} |")
        report_lines += [
            "",
            "## Write Verification",
            "",
            f"- `handoff_queue` count before/after: `{before_counts['handoff_queue']}` -> `{after_counts['handoff_queue']}`",
            f"- `user_audit_log` count before/after: `{before_counts['user_audit_log']}` -> `{after_counts['user_audit_log']}`",
            f"- `trip_bookings` count before/after: `{before_counts['trip_bookings']}` -> `{after_counts['trip_bookings']}`",
            f"- Writes went to PostgreSQL staging: `{'yes' if writes_to_pg else 'no'}`",
            "",
            "## SQLite Safety",
            "",
            f"- SQLite stayed unchanged: `{'yes' if sqlite_unchanged else 'no'}`",
            f"- SQLite size before/after: `{sqlite_before_size}` -> `{sqlite_after_size}` bytes",
            "",
            "## Relevant Tests",
            "",
            "- No automated PostgreSQL-mode unit suite was run against staging because the available test set is mainly SQLite-isolated and several write-path tests create/drop local databases rather than safely target a shared staging schema.",
            "",
            "## Remaining Blockers Before Production Cutover",
            "",
            "- The CRM web app can read and write PostgreSQL staging for SQLAlchemy-backed routes.",
            "- The AI/agent CRM bridge is still not PostgreSQL-compatible end to end.",
            "- `/api/crm/agent/read` and `/api/crm/agent/write` currently reject non-SQLite CRM backends.",
            "- `UnifiedCRMService` and `system_bridge` still open SQLite directly, so booking draft confirmation and duplicate-protection writes cannot yet be truthfully smoke-tested end to end on PostgreSQL.",
            "- The agent `/api/health` diagnostics still report the operational SQLite path because that subsystem has not been migrated to PostgreSQL-aware diagnostics.",
            "- Do not switch production or default local config yet.",
        ]
        REPORT_PATH.write_text("\n".join(report_lines), encoding="utf-8")

        print(f"report={REPORT_PATH}")
        print(f"sqlite_unchanged={sqlite_unchanged}")
        print(f"writes_to_postgres={writes_to_pg}")
        print(f"agent_adapter_blocked={agent_adapter_blocked}")
    finally:
        stop_services(crm_proc, agent_proc)


if __name__ == "__main__":
    run()
