from __future__ import annotations

import hmac
import os
from datetime import datetime, timezone
from urllib.parse import urlparse

from flask import Blueprint, flash, redirect, render_template, request, session, url_for
from werkzeug.security import check_password_hash, generate_password_hash

from app.extensions import db, limiter
from app.models.user import User
from app.models.user_audit import UserAuditLog
from app.security import generate_csrf_token


auth_bp = Blueprint("auth", __name__)


def _resolve_next_target(raw_next: str | None) -> str:
    target = (raw_next or "").strip()
    if not target:
        return url_for("admin.dashboard")

    parsed = urlparse(target)
    path = parsed.path or ""
    host_netloc = urlparse(request.host_url).netloc
    if parsed.netloc and parsed.netloc != host_netloc:
        return url_for("admin.dashboard")
    if path.startswith("/bookings/") and path.endswith("/status"):
        booking_id = path.split("/")[2] if len(path.split("/")) > 2 else ""
        if booking_id:
            return url_for("bookings.detail", booking_id=booking_id)
        return url_for("bookings.index")
    if path.startswith("/leads/") and path.endswith("/quick-action"):
        lead_id = path.split("/")[2] if len(path.split("/")) > 2 else ""
        if lead_id:
            return url_for("leads.detail", lead_id=lead_id)
        return url_for("leads.index")
    if path.startswith("/leads/") and path.endswith("/advance"):
        lead_id = path.split("/")[2] if len(path.split("/")) > 2 else ""
        if lead_id:
            return url_for("leads.detail", lead_id=lead_id)
        return url_for("leads.index")
    if path.startswith("/admin/handoffs/"):
        return url_for("handoffs.index")
    if path and path.startswith("/"):
        return target
    return url_for("admin.dashboard")


def _configured_admin_credentials() -> tuple[str, str, str]:
    username = (os.getenv("ADMIN_USERNAME", "") or os.getenv("CRM_ADMIN_USERNAME", "")).strip()
    password = os.getenv("ADMIN_PASSWORD", "").strip()
    password_hash = os.getenv("CRM_ADMIN_PASSWORD_HASH", "").strip()
    return username, password, password_hash


def _credentials_match(submitted_username: str, submitted_password: str) -> bool:
    configured_username, configured_password, configured_password_hash = _configured_admin_credentials()
    if not configured_username or not submitted_username or not submitted_password:
        return False
    if submitted_username.casefold() != configured_username.casefold():
        return False
    if configured_password:
        return hmac.compare_digest(configured_password, submitted_password)
    if configured_password_hash:
        return check_password_hash(configured_password_hash, submitted_password)
    return False


def _provision_login_user(username: str, password: str) -> User:
    user = User.query.filter(db.func.lower(User.username) == username.casefold()).first()
    timestamp = datetime.now(timezone.utc).replace(microsecond=0)
    if user is None:
        user = User(
            username=username,
            full_name=username,
            role="admin",
            is_active=True,
            password_hash=generate_password_hash(password),
            last_login_at=timestamp,
        )
        db.session.add(user)
    else:
        user.full_name = user.full_name or username
        user.role = "admin"
        user.is_active = True
        user.password_hash = generate_password_hash(password)
        user.last_login_at = timestamp
    db.session.commit()
    return user


def _record_login_event(user: User) -> None:
    details = f"username={user.username}; ip={request.headers.get('X-Forwarded-For', request.remote_addr or '').split(',')[0].strip() or 'unknown'}"
    db.session.add(
        UserAuditLog(
            actor_user_id=user.id,
            target_user_id=user.id,
            action="login",
            details=details,
        )
    )
    db.session.commit()


@auth_bp.route("/login", methods=["GET", "POST"])
@limiter.limit("10 per minute")
def login():
    error = ""
    if request.method == "POST":
        supplied_token = request.form.get("csrf_token", "")
        if supplied_token != session.get("csrf_token"):
            error = "Invalid username or password."
        else:
            username = (request.form.get("username") or "").strip()
            password = request.form.get("password") or ""
            if _credentials_match(username, password):
                user = _provision_login_user(username, password)
                _record_login_event(user)
                session.clear()
                session["logged_in"] = True
                session["user_id"] = user.id
                session["username"] = user.username
                session["role"] = user.role
                generate_csrf_token()
                flash(f"Welcome back, {user.display_name}.", "login_success")
                return redirect(_resolve_next_target(request.args.get("next")))
            error = "Invalid username or password."

    generate_csrf_token()
    return render_template("auth/login.html", error=error)


@auth_bp.route("/logout", methods=["POST"])
def logout():
    session.clear()
    flash("Logged out.", "info")
    return redirect(url_for("auth.login"))
