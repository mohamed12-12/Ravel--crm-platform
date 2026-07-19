from __future__ import annotations

import os
from urllib.parse import urlparse

from flask import Blueprint, flash, redirect, render_template, request, session, url_for
from werkzeug.security import check_password_hash

from app.models.user import User
from app.extensions import db
from app.security import generate_csrf_token
from datetime import datetime, timezone


auth_bp = Blueprint("auth", __name__)


def _resolve_next_target(raw_next: str | None) -> str:
    target = (raw_next or "").strip()
    if not target:
        return url_for("admin.dashboard")

    parsed = urlparse(target)
    path = parsed.path or ""
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


@auth_bp.route("/login", methods=["GET", "POST"])
def login():
    error = ""
    if request.method == "POST":
        supplied_token = request.form.get("csrf_token", "")
        if supplied_token != session.get("csrf_token"):
            error = "CRM service unavailable"
        else:
            username = (request.form.get("username") or "").strip()
            password = request.form.get("password") or ""
            ok = False

            user = (
                User.query.filter(db.func.lower(User.username) == username.casefold()).first()
                if username else None
            )
            if user and user.is_active and check_password_hash(user.password_hash, password):
                ok = True

            env_user = os.getenv("CRM_ADMIN_USERNAME", "").strip()
            env_hash = os.getenv("CRM_ADMIN_PASSWORD_HASH", "").strip()
            if not ok and user is None and env_user and env_hash and username.casefold() == env_user.casefold():
                ok = bool(password) and check_password_hash(env_hash, password)
                if ok:
                    user = User(
                        username=env_user,
                        full_name=env_user,
                        role=os.getenv("CRM_ADMIN_ROLE", "admin").strip().lower() or "admin",
                        is_active=True,
                        password_hash=env_hash,
                    )
                    db.session.add(user)
                    db.session.commit()

            if ok:
                user.last_login_at = datetime.now(timezone.utc).replace(microsecond=0)
                db.session.commit()
                session.clear()
                session["logged_in"] = True
                session["user_id"] = user.id
                session["username"] = user.username
                generate_csrf_token()
                flash("Changes saved", "success")
                return redirect(_resolve_next_target(request.args.get("next")))
            error = "Login required"

    generate_csrf_token()
    return render_template("auth/login.html", error=error)


@auth_bp.route("/logout", methods=["POST"])
def logout():
    session.clear()
    flash("Login required", "info")
    return redirect(url_for("auth.login"))
