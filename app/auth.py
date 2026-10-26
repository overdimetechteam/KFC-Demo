"""Login / logout, password change and simple user administration."""
import functools
import secrets

from flask import (Blueprint, abort, flash, g, redirect, render_template, request,
                   session, url_for)
from werkzeug.security import check_password_hash, generate_password_hash

from .db import audit, get_db

bp = Blueprint("auth", __name__)


# ------------------------------------------------------------------ CSRF
def csrf_token() -> str:
    if "_csrf" not in session:
        session["_csrf"] = secrets.token_urlsafe(32)
    return session["_csrf"]


def verify_csrf():
    if request.method in ("POST", "PUT", "DELETE"):
        sent = request.form.get("_csrf") or request.headers.get("X-CSRF-Token")
        if not sent or sent != session.get("_csrf"):
            abort(400, description="Invalid or missing CSRF token. Reload the page and try again.")


# ------------------------------------------------------------------ session helpers
def load_logged_in_user():
    uid = session.get("user_id")
    g.user = None
    if uid is not None:
        g.user = get_db().execute("SELECT * FROM users WHERE id = ? AND is_active = 1", (uid,)).fetchone()
        if g.user is None:
            session.clear()


def login_required(view):
    @functools.wraps(view)
    def wrapped(*args, **kwargs):
        if g.user is None:
            if request.path.startswith("/api/"):
                return {"error": "Session expired. Please log in again."}, 401
            return redirect(url_for("auth.login", next=request.full_path))
        return view(*args, **kwargs)
    return wrapped


def admin_required(view):
    @functools.wraps(view)
    @login_required
    def wrapped(*args, **kwargs):
        if g.user["role"] != "admin":
            abort(403)
        return view(*args, **kwargs)
    return wrapped


# ------------------------------------------------------------------ routes
@bp.route("/login", methods=["GET", "POST"])
def login():
    if g.user:
        return redirect(url_for("main.dashboard"))
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")
        user = get_db().execute("SELECT * FROM users WHERE username = ?", (username,)).fetchone()
        if user is None or not check_password_hash(user["password_hash"], password):
            audit(None, "login_failed", username)
            flash("Incorrect username or password.", "error")
        elif not user["is_active"]:
            flash("This account is disabled. Contact an administrator.", "error")
        else:
            session.clear()
            session.permanent = True
            session["user_id"] = user["id"]
            db = get_db()
            db.execute("UPDATE users SET last_login = datetime('now','localtime') WHERE id = ?", (user["id"],))
            db.commit()
            audit(user["id"], "login")
            nxt = request.args.get("next", "")
            if not nxt.startswith("/") or nxt.startswith("//"):
                nxt = url_for("main.dashboard")
            return redirect(nxt)
    return render_template("login.html")


@bp.route("/logout", methods=["POST"])
def logout():
    if g.user:
        audit(g.user["id"], "logout")
    session.clear()
    flash("You have been logged out.", "success")
    return redirect(url_for("auth.login"))


@bp.route("/account/password", methods=["GET", "POST"])
@login_required
def change_password():
    if request.method == "POST":
        current = request.form.get("current_password", "")
        new = request.form.get("new_password", "")
        confirm = request.form.get("confirm_password", "")
        if not check_password_hash(g.user["password_hash"], current):
            flash("Current password is incorrect.", "error")
        elif len(new) < 8:
            flash("New password must be at least 8 characters.", "error")
        elif new != confirm:
            flash("New passwords do not match.", "error")
        else:
            db = get_db()
            db.execute("UPDATE users SET password_hash = ? WHERE id = ?", (generate_password_hash(new), g.user["id"]))
            db.commit()
            audit(g.user["id"], "password_changed")
            flash("Password updated.", "success")
            return redirect(url_for("main.dashboard"))
    return render_template("change_password.html")


@bp.route("/users", methods=["GET", "POST"])
@admin_required
def users():
    db = get_db()
    if request.method == "POST":
        action = request.form.get("action")
        if action == "create":
            username = request.form.get("username", "").strip()
            password = request.form.get("password", "")
            role = "admin" if request.form.get("role") == "admin" else "user"
            if not username or len(password) < 8:
                flash("Username is required and password must be at least 8 characters.", "error")
            elif db.execute("SELECT 1 FROM users WHERE username = ?", (username,)).fetchone():
                flash(f"User '{username}' already exists.", "error")
            else:
                db.execute("INSERT INTO users (username, full_name, password_hash, role) VALUES (?, ?, ?, ?)",
                           (username, request.form.get("full_name", "").strip(),
                            generate_password_hash(password), role))
                db.commit()
                audit(g.user["id"], "user_created", username)
                flash(f"User '{username}' created.", "success")
        elif action in ("disable", "enable", "reset"):
            uid = int(request.form.get("user_id", 0))
            if uid == g.user["id"] and action == "disable":
                flash("You cannot disable your own account.", "error")
            elif action == "reset":
                pwd = request.form.get("password", "")
                if len(pwd) < 8:
                    flash("Password must be at least 8 characters.", "error")
                else:
                    db.execute("UPDATE users SET password_hash = ? WHERE id = ?", (generate_password_hash(pwd), uid))
                    db.commit()
                    flash("Password reset.", "success")
            else:
                db.execute("UPDATE users SET is_active = ? WHERE id = ?", (1 if action == "enable" else 0, uid))
                db.commit()
                flash("User updated.", "success")
        return redirect(url_for("auth.users"))
    rows = db.execute("SELECT * FROM users ORDER BY username").fetchall()
    return render_template("users.html", users=rows)
