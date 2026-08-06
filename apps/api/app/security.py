from __future__ import annotations

import hmac
import os
import secrets
from functools import wraps

from flask import current_app, flash, jsonify, redirect, request, session, url_for
from app.extensions import db
from app.models.user import User


WRITE_METHODS = {"POST", "PUT", "PATCH", "DELETE"}
SENSITIVE_READ_MARKERS = ("/documents", "/export")
ADMIN_WRITE_PREFIXES = (
    "/admin/import",
    "/admin/duplicates/merge",
    "/admin/sync-issues/retry",
    "/api/crm/resolve-identity",
)
CRM_WRITE_ROLES = {"admin", "manager", "agent", "sales"}
PROTECTED_BROWSER_BLUEPRINTS = {"admin", "travelers", "leads", "bookings", "trips", "interactions", "handoffs"}
PUBLIC_BROWSER_ENDPOINTS = {"auth.login", "auth.logout", "static", "trips.public_media", "api_docs.openapi_contract"}
ROLE_PERMISSIONS = {
    "admin": {"view_all", "manage_users", "assign_work", "update_followup", "manage_handoffs"},
    "manager": {"view_all", "assign_work", "update_followup", "manage_handoffs"},
    "agent": {"update_followup"},
    "sales": {"update_followup"},
}
USER_ADMIN_PREFIXES = ("/admin/users",)


def _configured_token() -> str:
    return str(current_app.config.get("CRM_API_TOKEN") or os.getenv("CRM_API_TOKEN", "")).strip()


def _api_token_authenticated() -> bool:
    configured = _configured_token()
    supplied = request.headers.get("X-CRM-API-Key", "").strip()
    authorization = request.headers.get("Authorization", "")
    if authorization.lower().startswith("bearer "):
        supplied = authorization[7:].strip()
    return bool(configured and supplied and hmac.compare_digest(configured, supplied))


def _session_authenticated() -> bool:
    return current_user() is not None


def _authenticated() -> bool:
    return _session_authenticated() or _api_token_authenticated()


def _browser_auth_required() -> bool:
    endpoint = str(request.endpoint or "").strip()
    if not endpoint or endpoint in PUBLIC_BROWSER_ENDPOINTS:
        return False
    blueprint = str(request.blueprint or "").strip()
    if blueprint in PROTECTED_BROWSER_BLUEPRINTS:
        return True
    if endpoint == "index":
        return True
    return False


def _api_token_role() -> str:
    """Role for API-token callers, resolved server-side from the credential
    itself rather than trusted from the client-supplied X-CRM-Role header.
    """
    configured = str(current_app.config.get("CRM_API_TOKEN_ROLE") or os.getenv("CRM_API_TOKEN_ROLE", "") or "agent")
    return configured.strip().lower() or "agent"


def _role() -> str:
    user = current_user()
    if user:
        return str(user.role or "").strip().lower()
    if _api_token_authenticated():
        return _api_token_role()
    return ""


def _requires_admin() -> bool:
    return request.path == "/api/reset" or request.path.startswith(ADMIN_WRITE_PREFIXES)


def _requires_user_admin() -> bool:
    return request.path.startswith(USER_ADMIN_PREFIXES)


def current_user() -> User | None:
    raw_id = session.get("user_id")
    try:
        user_id = int(raw_id)
    except (TypeError, ValueError):
        user_id = None
    user = db.session.get(User, user_id) if user_id is not None else None
    if user and user.is_active:
        return user

    if not session.get("logged_in"):
        return None
    fallback_username = str(session.get("username") or os.getenv("ADMIN_USERNAME", "") or os.getenv("CRM_ADMIN_USERNAME", "")).strip()
    if not fallback_username:
        return None
    fallback = db.session.query(User).filter(db.func.lower(User.username) == fallback_username.casefold()).first()
    return fallback if fallback and fallback.is_active else None


def current_user_id() -> int | None:
    user = current_user()
    return user.id if user else None


def current_actor() -> str:
    user = current_user()
    if user:
        return user.username
    if _api_token_authenticated():
        return str(request.headers.get("X-CRM-Actor") or "system-api").strip() or "system-api"
    return "system-ui"


def current_role() -> str:
    return _role()


def has_permission(permission: str) -> bool:
    return permission in ROLE_PERMISSIONS.get(current_role(), set())


def can_view_all_records() -> bool:
    """Full-access roles (admin, manager) may view any record regardless of assignment."""
    return has_permission("view_all")


def can_view_assigned_record(assigned_to_user_id: int | None) -> bool:
    """Non-full-access callers may view a record if it is unassigned or assigned to them.

    Unassigned records stay visible to every agent because the bookings queue
    already treats "unassigned" as a claimable, browsable state (see
    bookings.py's assigned_to_user_id.is_(None) filter) rather than a
    restricted one.
    """
    if can_view_all_records():
        return True
    if assigned_to_user_id is None:
        return True
    return assigned_to_user_id == current_user_id()


def permission_required(permission: str):
    def decorator(view):
        @wraps(view)
        def guarded(*args, **kwargs):
            if not _authenticated():
                if _wants_json():
                    return jsonify({"error": "authentication_required"}), 401
                return redirect(url_for("auth.login", next=request.url))
            if not has_permission(permission):
                if _wants_json():
                    return jsonify({"error": "forbidden"}), 403
                return _browser_redirect("You do not have permission", "error", status_code=403)
            return view(*args, **kwargs)
        return guarded
    return decorator


def employee_session_guard(*, require_csrf: bool = True):
    """Require a real CRM employee browser session, not an API token."""
    wants_json = _wants_json()
    if not _session_authenticated():
        if wants_json:
            return jsonify({"error": "employee_authentication_required"}), 401
        flash("Employee login required", "error")
        return redirect(url_for("auth.login", next=request.url))
    if _role() not in CRM_WRITE_ROLES:
        if wants_json:
            return jsonify({"error": "forbidden"}), 403
        return _browser_redirect("You do not have permission", "error", status_code=403)
    if require_csrf and request.method in WRITE_METHODS and not _csrf_valid():
        if wants_json:
            return jsonify({"error": "csrf_required"}), 400
        return _browser_redirect("CRM service unavailable", "error", status_code=400)
    return None


def employee_session_required(view):
    @wraps(view)
    def guarded(*args, **kwargs):
        denied = employee_session_guard(require_csrf=True)
        if denied is not None:
            return denied
        return view(*args, **kwargs)

    return guarded


def generate_csrf_token() -> str:
    token = session.get("csrf_token")
    if not token:
        token = secrets.token_urlsafe(32)
        session["csrf_token"] = token
    return str(token)


def _csrf_token_from_request() -> str:
    return (
        request.form.get("csrf_token")
        or request.headers.get("X-CSRF-Token")
        or request.headers.get("X-CSRFToken")
        or ""
    ).strip()


def _csrf_valid() -> bool:
    expected = str(session.get("csrf_token") or "").strip()
    supplied = _csrf_token_from_request()
    return bool(expected and supplied and hmac.compare_digest(expected, supplied))


def _wants_json() -> bool:
    if request.is_json or request.path.startswith("/api/"):
        return True
    accept = request.headers.get("Accept", "")
    return "application/json" in accept and "text/html" not in accept


def _browser_redirect(message: str, category: str, endpoint: str = "admin.dashboard", status_code: int = 302):
    flash(message, category)
    target = request.referrer or url_for(endpoint)
    response = redirect(target)
    response.status_code = status_code
    return response


def crm_request_guard():
    """Protect CRM writes when enabled; development tests can opt out explicitly."""
    is_sensitive_read = request.method == "GET" and any(
        marker in request.path for marker in SENSITIVE_READ_MARKERS
    )
    if request.path in {"/login", "/api/auth/login", "/logout", "/api/auth/logout"}:
        return None
    if not current_app.config.get("CRM_AUTH_ENABLED", False):
        return None
    wants_json = _wants_json()
    if request.method == "GET" and _browser_auth_required() and not _authenticated():
        flash("Login required", "error")
        return redirect(url_for("auth.login", next=request.url))
    if request.method not in WRITE_METHODS and not is_sensitive_read:
        return None
    if not _authenticated():
        if not wants_json:
            flash("Login required", "error")
            return redirect(url_for("auth.login", next=request.url))
        return jsonify({"error": "authentication_required"}), 401
    if request.method in WRITE_METHODS and _role() not in CRM_WRITE_ROLES:
        if not wants_json:
            return _browser_redirect("You do not have permission", "error", status_code=403)
        return jsonify({"error": "forbidden"}), 403
    if _requires_user_admin() and _role() != "admin":
        if not wants_json:
            return _browser_redirect("You do not have permission", "error", status_code=403)
        return jsonify({"error": "forbidden"}), 403
    if _requires_admin() and _role() not in {"admin", "manager"}:
        if not wants_json:
            return _browser_redirect("You do not have permission", "error", status_code=403)
        return jsonify({"error": "forbidden"}), 403
    if request.method in WRITE_METHODS and _session_authenticated() and not _api_token_authenticated():
        if not _csrf_valid():
            if not wants_json:
                return _browser_redirect("CRM service unavailable", "error", status_code=400)
            return jsonify({"error": "csrf_required"}), 400
    return None


def require_crm_auth(view):
    @wraps(view)
    def guarded(*args, **kwargs):
        if not _authenticated():
            return jsonify({"error": "authentication_required"}), 401
        return view(*args, **kwargs)

    return guarded
