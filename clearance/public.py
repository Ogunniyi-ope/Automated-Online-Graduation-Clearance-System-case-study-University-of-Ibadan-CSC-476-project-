"""Public pages: home redirect and certificate verification (no login needed)."""
from flask import Blueprint, redirect, render_template, request, url_for

from . import certificate
from .auth import ROLE_HOME, current_user

bp = Blueprint("public", __name__)


@bp.route("/")
def index():
    user = current_user()
    return redirect(url_for(ROLE_HOME[user["role"]]) if user else url_for("auth.login"))


@bp.route("/verify")
def verify():
    number = request.args.get("no", "").strip()
    result = certificate.verify(number) if number else None
    return render_template("verify.html", number=number, result=result)
