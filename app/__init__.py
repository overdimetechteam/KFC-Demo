"""Three-Way Match application factory."""
import datetime as dt
import os
import secrets

from flask import Flask, g, render_template

from .doc_config import MONEY_FIELDS, QTY_FIELDS, label


def _secret_key(instance_path: str) -> str:
    if os.environ.get("SECRET_KEY"):
        return os.environ["SECRET_KEY"]
    path = os.path.join(instance_path, "secret_key")
    if not os.path.exists(path):
        with open(path, "w") as fh:
            fh.write(secrets.token_hex(32))
    with open(path) as fh:
        return fh.read().strip()


def create_app(test_config: dict | None = None) -> Flask:
    project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    app = Flask(__name__, instance_path=os.path.join(project_root, "instance"), instance_relative_config=True)
    os.makedirs(app.instance_path, exist_ok=True)

    app.config.update(
        SECRET_KEY=_secret_key(app.instance_path),
        DATABASE=os.path.join(app.instance_path, "threeway.sqlite3"),
        MAX_CONTENT_LENGTH=25 * 1024 * 1024,           # 25 MB per request
        PERMANENT_SESSION_LIFETIME=dt.timedelta(hours=8),
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE="Lax",
        DEFAULT_ADMIN_USER=os.environ.get("TWM_ADMIN_USER", "admin"),
        DEFAULT_ADMIN_PASSWORD=os.environ.get("TWM_ADMIN_PASSWORD", "admin123"),
        APP_NAME="Three-Way Match",
    )
    if test_config:
        app.config.update(test_config)

    from . import auth, db, routes, vouchers
    db.init_db(app)

    @app.before_request
    def _before():
        auth.load_logged_in_user()
        auth.verify_csrf()

    app.register_blueprint(auth.bp)
    app.register_blueprint(routes.bp)
    app.register_blueprint(vouchers.bp)

    # ------------------------------------------------------------ template helpers
    @app.context_processor
    def _ctx():
        return {"csrf_token": auth.csrf_token, "label": label, "app_name": app.config["APP_NAME"]}

    @app.template_filter("fmt")
    def _fmt(value, field=None):
        if value is None or value == "":
            return "—"
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            if field in MONEY_FIELDS or (field is None and isinstance(value, float) and not value.is_integer()):
                return f"{value:,.2f}"
            if field in QTY_FIELDS or float(value).is_integer():
                return f"{value:,.0f}" if float(value).is_integer() else f"{value:,.2f}"
            return f"{value:,.2f}"
        return value

    @app.template_filter("status_class")
    def _status_class(status):
        return {"PASS": "ok", "FAIL": "bad", "WARN": "warn", "INFO": "info",
                "MATCHED": "ok", "MATCHED WITH WARNINGS": "warn", "MISMATCH": "bad",
                "INCOMPLETE": "warn"}.get(status, "info")

    @app.errorhandler(400)
    @app.errorhandler(403)
    @app.errorhandler(404)
    @app.errorhandler(413)
    def _err(e):
        code = getattr(e, "code", 500)
        msg = {413: "The uploaded file is too large (limit 25 MB)."}.get(code, getattr(e, "description", str(e)))
        return render_template("error.html", code=code, message=msg), code

    return app
