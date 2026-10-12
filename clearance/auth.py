"""Authentication, role-based access control, CSRF protection and login lock-out."""
import functools
import hmac
from datetime import datetime, timedelta

from flask import (Blueprint, abort, current_app, flash, g, redirect,
                   render_template, request, session, url_for)
from werkzeug.security import check_password_hash, generate_password_hash

from .db import WAT, audit, execute, now, query

bp = Blueprint("auth", __name__)

ROLE_HOME = {
    "student": "student.dashboard",
    "officer": "officer.queue",
    "records": "officer.queue",
    "admin": "admin.dashboard",
}


# ---------------------------------------------------------------- CSRF
def csrf_protect():
    if request.method in ("POST", "PUT", "PATCH", "DELETE"):
        sent = request.form.get("csrf_token", "") or request.headers.get("X-CSRF-Token", "")
        expected = session.get("csrf", "")
        if not expected or not hmac.compare_digest(sent, expected):
            abort(400)


# ---------------------------------------------------------------- current user
def current_user():
    if "user" in g:
        return g.user
    uid = session.get("user_id")
    g.user = None
    if uid:
        row = query(
            "SELECT u.*, cu.unit_name FROM users u LEFT JOIN clearance_units cu ON cu.unit_id = u.unit_id "
            "WHERE u.user_id = ? AND u.is_active = 1", (uid,), one=True)
        if row is not None and session.get("nonce") != row["session_nonce"]:
            row = None                      # cookie from before a logout / password change
        g.user = row
        if row is None:
            session.clear()
    return g.user


def login_required(*roles):
    """Decorator: user must be signed in and (optionally) hold one of `roles`."""
    def deco(view):
        @functools.wraps(view)
        def wrapped(*a, **kw):
            user = current_user()
            if user is None:
                return redirect(url_for("auth.login"))
            if roles and user["role"] not in roles:
                abort(403)
            return view(*a, **kw)
        return wrapped
    return deco


def hash_password(plain: str) -> str:
    return generate_password_hash(plain)   # scrypt with a per-user salt


def password_problem(pw: str):
    if len(pw) < 8:
        return "Password must be at least 8 characters long."
    if pw.lower() == pw or pw.upper() == pw or not any(c.isdigit() for c in pw):
        return "Password must contain upper-case and lower-case letters and a digit."
    return None


# ---------------------------------------------------------------- views
@bp.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")
        user = query("SELECT * FROM users WHERE username = ?", (username,), one=True)
        generic = "Invalid ID or password."
        if user is not None and user["locked_until"]:
            if datetime.strptime(user["locked_until"], "%Y-%m-%d %H:%M:%S") > datetime.now(WAT).replace(tzinfo=None):
                audit(user["user_id"], "login_blocked", "account temporarily locked")
                flash("Too many failed attempts. Try again in a few minutes.", "danger")
                return render_template("login.html"), 429
        if user is not None and user["is_active"] and check_password_hash(user["password_hash"], password):
            execute("UPDATE users SET failed_attempts = 0, locked_until = NULL WHERE user_id = ?", (user["user_id"],))
            session.clear()
            session.permanent = True
            session["user_id"] = user["user_id"]
            session["nonce"] = user["session_nonce"]
            from secrets import token_hex
            session["csrf"] = token_hex(16)
            audit(user["user_id"], "login", "")
            return redirect(url_for(ROLE_HOME[user["role"]]))
        if user is not None:
            attempts = user["failed_attempts"] + 1
            locked = None
            if attempts >= current_app.config["MAX_FAILED_LOGINS"]:
                locked = (datetime.now(WAT).replace(tzinfo=None)
                          + timedelta(minutes=current_app.config["LOCKOUT_MINUTES"])).strftime("%Y-%m-%d %H:%M:%S")
                attempts = 0
            execute("UPDATE users SET failed_attempts = ?, locked_until = ? WHERE user_id = ?",
                    (attempts, locked, user["user_id"]))
            audit(user["user_id"], "login_failed", "")
        flash(generic, "danger")
        return render_template("login.html"), 401
    if current_user():
        return redirect(url_for(ROLE_HOME[current_user()["role"]]))
    return render_template("login.html")


@bp.route("/logout", methods=["POST"])
def logout():
    uid = session.get("user_id")
    if uid:
        audit(uid, "logout", "")
        execute("UPDATE users SET session_nonce = session_nonce + 1 WHERE user_id = ?", (uid,))
    session.clear()
    return redirect(url_for("auth.login"))


@bp.route("/password", methods=["GET", "POST"])
@login_required()
def change_password():
    user = current_user()
    if request.method == "POST":
        old, new = request.form.get("old", ""), request.form.get("new", "")
        if not check_password_hash(user["password_hash"], old):
            flash("Current password is incorrect.", "danger")
        elif (problem := password_problem(new)):
            flash(problem, "danger")
        else:
            execute("UPDATE users SET password_hash = ?, session_nonce = session_nonce + 1 WHERE user_id = ?",
                    (hash_password(new), user["user_id"]))
            session["nonce"] = user["session_nonce"] + 1
            audit(user["user_id"], "password_changed", "")
            flash("Password changed.", "success")
            return redirect(url_for(ROLE_HOME[user["role"]]))
    return render_template("password.html")


@bp.route("/notifications")
@login_required()
def notifications():
    user = current_user()
    rows = query("SELECT * FROM notifications WHERE user_id = ? ORDER BY notif_id DESC LIMIT 100", (user["user_id"],))
    execute("UPDATE notifications SET is_read = 1 WHERE user_id = ?", (user["user_id"],))
    return render_template("notifications.html", rows=rows)
