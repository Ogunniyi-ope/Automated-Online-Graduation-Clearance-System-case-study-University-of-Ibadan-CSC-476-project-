"""Clearance officer portal (also used by the Records officer, who additionally sees the cleared list)."""
import os

from flask import (Blueprint, abort, current_app, flash, redirect, render_template, request,
                   send_from_directory, url_for)

from . import workflow
from .auth import current_user, login_required
from .db import query

bp = Blueprint("officer", __name__, url_prefix="/officer")
ROLES = ("officer", "records")


@bp.route("/")
@login_required(*ROLES)
def queue():
    user = current_user()
    pending = query(
        "SELECT st.step_id, st.opened_at, r.matric_no, u.full_name, s.department, s.faculty, "
        "(SELECT COUNT(*) FROM obligations o WHERE o.matric_no = r.matric_no AND o.unit_id = st.unit_id AND o.is_cleared = 0) AS owed "
        "FROM clearance_steps st JOIN clearance_requests r ON r.request_id = st.request_id "
        "JOIN students s ON s.matric_no = r.matric_no JOIN users u ON u.user_id = s.user_id "
        "WHERE st.unit_id = ? AND st.status = 'pending' ORDER BY st.opened_at", (user["unit_id"],))
    recent = query(
        "SELECT st.step_id, st.status, st.decided_at, st.remark, r.matric_no, u.full_name FROM clearance_steps st "
        "JOIN clearance_requests r ON r.request_id = st.request_id JOIN students s ON s.matric_no = r.matric_no "
        "JOIN users u ON u.user_id = s.user_id WHERE st.unit_id = ? AND st.status IN ('approved', 'rejected') "
        "ORDER BY st.decided_at DESC LIMIT 10", (user["unit_id"],))
    return render_template("officer_queue.html", pending=pending, recent=recent)


def _step_for_unit(step_id):
    user = current_user()
    step = query(
        "SELECT st.*, r.matric_no, r.request_id AS rid, u.full_name, s.department, s.faculty, s.level, s.session, s.hall, "
        "cu.unit_name FROM clearance_steps st JOIN clearance_requests r ON r.request_id = st.request_id "
        "JOIN students s ON s.matric_no = r.matric_no JOIN users u ON u.user_id = s.user_id "
        "JOIN clearance_units cu ON cu.unit_id = st.unit_id WHERE st.step_id = ?", (step_id,), one=True)
    if step is None:
        abort(404)
    if step["unit_id"] != user["unit_id"]:
        abort(403)
    return step


@bp.route("/request/<int:step_id>")
@login_required(*ROLES)
def detail(step_id):
    step = _step_for_unit(step_id)
    obligations = query("SELECT * FROM obligations WHERE matric_no = ? AND unit_id = ? ORDER BY is_cleared, obligation_id",
                        (step["matric_no"], step["unit_id"]))
    docs = query("SELECT * FROM documents WHERE request_id = ? ORDER BY doc_id DESC", (step["rid"],))
    return render_template("officer_detail.html", step=step, obligations=obligations, docs=docs)


@bp.route("/decide/<int:step_id>", methods=["POST"])
@login_required(*ROLES)
def decide(step_id):
    _step_for_unit(step_id)
    decision = "approved" if request.form.get("decision") == "approve" else "rejected"
    try:
        workflow.decide(step_id, current_user(), decision, request.form.get("remark", ""))
        flash("Request approved." if decision == "approved" else "Request rejected; the student has been notified.", "success")
        return redirect(url_for("officer.queue"))
    except workflow.RuleError as exc:
        flash(str(exc), "danger")
    except PermissionError:
        abort(403)
    return redirect(url_for("officer.detail", step_id=step_id))


@bp.route("/obligations", methods=["POST"])
@login_required(*ROLES)
def add_obligation():
    back = request.form.get("back") or url_for("officer.queue")
    if not back.startswith("/officer/"):          # never redirect to an outside address
        back = url_for("officer.queue")
    try:
        workflow.add_obligation(current_user(), request.form.get("matric_no", "").strip(),
                                request.form.get("description"), request.form.get("amount"))
        flash("Obligation recorded.", "success")
    except workflow.RuleError as exc:
        flash(str(exc), "danger")
    return redirect(back)


@bp.route("/obligations/<int:oid>/clear", methods=["POST"])
@login_required(*ROLES)
def clear_obligation(oid):
    back = request.form.get("back") or url_for("officer.queue")
    if not back.startswith("/officer/"):
        back = url_for("officer.queue")
    try:
        workflow.clear_obligation(current_user(), oid)
        flash("Obligation cleared.", "success")
    except workflow.RuleError as exc:
        flash(str(exc), "danger")
    except PermissionError:
        abort(403)
    return redirect(back)


@bp.route("/documents/<int:doc_id>")
@login_required(*ROLES)
def document(doc_id):
    user = current_user()
    row = query(
        "SELECT d.* FROM documents d JOIN clearance_steps st ON st.request_id = d.request_id "
        "WHERE d.doc_id = ? AND st.unit_id = ? AND st.status IN ('pending', 'approved', 'rejected')",
        (doc_id, user["unit_id"]), one=True)
    if row is None:
        abort(404)
    return send_from_directory(current_app.config["UPLOAD_DIR"], row["file_path"], as_attachment=True,
                               download_name=row["original_name"])


@bp.route("/students")
@login_required(*ROLES)
def students():
    """Search students (to record an obligation)."""
    q = request.args.get("q", "").strip()
    rows = []
    if q:
        like = "%" + q.replace("%", "").replace("_", "") + "%"
        rows = query("SELECT s.matric_no, u.full_name, s.department FROM students s JOIN users u ON u.user_id = s.user_id "
                     "WHERE s.matric_no LIKE ? OR u.full_name LIKE ? ORDER BY u.full_name LIMIT 25", (like, like))
    return render_template("officer_students.html", rows=rows, q=q)


@bp.route("/cleared")
@login_required("records")
def cleared():
    rows = query(
        "SELECT r.certificate_no, r.cleared_at, r.matric_no, u.full_name, s.department, s.faculty "
        "FROM clearance_requests r JOIN students s ON s.matric_no = r.matric_no JOIN users u ON u.user_id = s.user_id "
        "WHERE r.overall_status = 'cleared' ORDER BY r.cleared_at DESC")
    return render_template("officer_cleared.html", rows=rows)
