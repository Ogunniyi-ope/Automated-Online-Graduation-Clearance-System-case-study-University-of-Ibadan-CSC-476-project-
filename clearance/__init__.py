"""Automated Online Graduation Clearance System (case study: University of Ibadan)."""
import os
import secrets
from datetime import timedelta

import click
from flask import Flask, render_template, session

from . import db


def create_app(test_config=None):
    app = Flask(__name__, instance_relative_config=True)
    app.config.from_mapping(
        SECRET_KEY=os.environ.get("SECRET_KEY", "dev-only-change-me"),
        CERT_KEY=os.environ.get("CERT_KEY", "dev-only-certificate-key"),
        DATABASE=os.path.join(app.instance_path, "clearance.sqlite"),
        UPLOAD_DIR=os.path.join(app.instance_path, "uploads"),
        MAX_UPLOAD_BYTES=2 * 1024 * 1024,
        MAX_CONTENT_LENGTH=3 * 1024 * 1024,
        PERMANENT_SESSION_LIFETIME=timedelta(minutes=30),   # inactivity timeout
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE="Lax",
        SESSION_COOKIE_SECURE=os.environ.get("COOKIE_SECURE", "0") == "1",
        FINAL_YEAR_LEVEL=400,
        MAX_FAILED_LOGINS=5,
        LOCKOUT_MINUTES=15,
        BASE_URL=os.environ.get("BASE_URL", "http://127.0.0.1:5000"),
        SMTP_HOST=os.environ.get("SMTP_HOST"),
        SMTP_PORT=int(os.environ.get("SMTP_PORT", "587")),
        SMTP_USER=os.environ.get("SMTP_USER"),
        SMTP_PASSWORD=os.environ.get("SMTP_PASSWORD"),
        MAIL_FROM=os.environ.get("MAIL_FROM", "clearance@example.edu.ng"),
        TESTING=False,
    )
    if test_config:
        app.config.update(test_config)

    os.makedirs(app.instance_path, exist_ok=True)
    os.makedirs(app.config["UPLOAD_DIR"], exist_ok=True)

    app.teardown_appcontext(db.close_db)

    from . import auth, student, officer, admin, public
    app.register_blueprint(auth.bp)
    app.register_blueprint(student.bp)
    app.register_blueprint(officer.bp)
    app.register_blueprint(admin.bp)
    app.register_blueprint(public.bp)

    @app.before_request
    def _csrf_guard():
        auth.csrf_protect()

    @app.after_request
    def _security_headers(resp):
        resp.headers.setdefault("X-Content-Type-Options", "nosniff")
        resp.headers.setdefault("X-Frame-Options", "DENY")
        resp.headers.setdefault("Referrer-Policy", "same-origin")
        resp.headers.setdefault(
            "Content-Security-Policy",
            "default-src 'self'; img-src 'self' data:; style-src 'self'; script-src 'self'; frame-ancestors 'none'",
        )
        if resp.mimetype == "text/html":
            resp.headers.setdefault("Cache-Control", "no-store")
        return resp

    @app.context_processor
    def _inject():
        if "csrf" not in session:
            session["csrf"] = secrets.token_hex(16)
        return {"csrf_token": session["csrf"], "current_user": auth.current_user()}

    @app.errorhandler(403)
    def _forbidden(_e):
        return render_template("error.html", code=403, message="You do not have permission to open this page."), 403

    @app.errorhandler(404)
    def _not_found(_e):
        return render_template("error.html", code=404, message="The page you asked for does not exist."), 404

    @app.errorhandler(413)
    def _too_large(_e):
        return render_template("error.html", code=413, message="The uploaded file is too large (maximum 2 MB)."), 413

    @app.errorhandler(400)
    def _bad(_e):
        return render_template("error.html", code=400, message="The request could not be processed. Please go back and try again."), 400

    @app.cli.command("init-db")
    def _init_db_cmd():
        """Create the database tables."""
        db.init_db()
        click.echo("Database initialised.")

    @app.cli.command("seed-demo")
    def _seed_cmd():
        """Load clearance units, demo staff and 30 demo students."""
        from .seed import seed_demo
        db.init_db()
        seed_demo()
        click.echo("Demo data loaded. See README for demo logins.")

    return app
