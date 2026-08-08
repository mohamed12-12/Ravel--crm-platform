"""Single shared-password login for the legacy/demo web UI and api_routes.py
-- APP_PASSWORD_HASH (preferred) or APP_PASSWORD wins if set; the
DEBUG-only APP_DEMO_PASSWORD fallback (default "rahma2026") only applies
when neither is configured, so it never silently activates in production.
"""
from functools import wraps
import hmac
import os
from werkzeug.security import check_password_hash
from flask import request, redirect, session, render_template, Blueprint, current_app

auth_bp = Blueprint("auth", __name__)

def login_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not session.get("logged_in"):
            return redirect("/login")
        return f(*args, **kwargs)
    return decorated_function

@auth_bp.route("/login", methods=["GET", "POST"])
def login():
    error = None
    if request.method == "POST":
        password = request.form.get("password")
        password_hash = os.environ.get("APP_PASSWORD_HASH", "").strip()
        password_value = os.environ.get("APP_PASSWORD", "").strip()
        demo_password = os.environ.get("APP_DEMO_PASSWORD", "").strip()
        if password_hash:
            ok = bool(password) and check_password_hash(password_hash, password)
        elif password_value:
            ok = bool(password) and hmac.compare_digest(password_value, password or "")
        elif current_app.config.get("DEBUG"):
            ok = bool(password) and hmac.compare_digest(demo_password or "rahma2026", password or "")
        else:
            ok = False
        if ok:
            session["logged_in"] = True
            return redirect("/")
        else:
            error = "Invalid password"
    return render_template("login.html", error=error)

@auth_bp.route("/logout")
def logout():
    session.pop("logged_in", None)
    return redirect("/login")
