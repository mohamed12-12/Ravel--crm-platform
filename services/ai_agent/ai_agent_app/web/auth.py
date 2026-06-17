from functools import wraps
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
        # Demo-only single password auth.
        # TODO(production): replace with operator accounts, RBAC, audit logs, and login rate limiting.
        password = request.form.get("password")
        if password == "rahma2026": # Default demo password
            session["logged_in"] = True
            return redirect("/crm")
        else:
            error = "Invalid password"
    return render_template("login.html", error=error)

@auth_bp.route("/logout")
def logout():
    session.pop("logged_in", None)
    return redirect("/login")
