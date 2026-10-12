"""Student portal: apply, upload documents, track status, download certificate."""
import os
import uuid

from flask import (Blueprint, abort, current_app, flash, redirect, render_template, request,
                   send_file, url_for, Response)
from werkzeug.utils import secure_filename

from . import certificate, workflow
from .auth import current_user, login_required
from .db import audit, execute, now, query

bp = Blueprint("student", __name__, url_prefix="/student")

ALLOWED = {
    "pdf": b"%PDF",
    "png": b"\x89PNG\r\n\x1a\n",
    "jpg": b"\xff\xd8\xff",
    "jpeg": b"\xff\xd8\xff",
}
DOC_TYPES = ["Payment receipt", "Library clearance slip", "Hostel clearance slip", "Medical record", "Other"]


def _student_context():
    user = current_user()
    student = workflow.get_student(user["user_id"])
    req = query("SELECT * FROM clearance_requests WHERE matric_no = ?", (student["matric_no"],), one=True) if student else None
    steps, docs = [], []
    if req:
        steps = query(
            "SELECT st.*, cu.unit_name, cu.sequence_no FROM clearance_steps st "
            "JOIN clearance_units cu ON cu.unit_id = st.unit_id WHERE st.request_id = ? ORDER BY cu.sequence_no",
            (req["request_id"],))
        docs = query("SELECT * FROM documents WHERE request_id = ? ORDER BY doc_id DESC", (req["request_id"],))
    obligations = query(
        "SELECT o.*, cu.unit_name FROM obligations o JOIN clearance_units cu ON cu.unit_id = o.unit_id "
        "WHERE o.matric_no = ? ORDER BY o.is_cleared, o.obligation_id", (student["matric_no"],)) if student else []
    return user, student, req, steps, docs, obligations


@bp.route("/")
@login_required("student")
def dashboard():
    user, student, req, steps, docs, obligations = _student_context()
    ok, reason = workflow.eligibility(student)
    approved = sum(1 for s in steps if s["status"] == "approved")
    qr = None
    if req and req["overall_status"] == "cleared":
        qr = certificate.qr_svg(certificate.verify_url(req["certificate_no"]), 3)
    return render_template("student_dashboard.html", student=student, req=req, steps=steps, docs=docs,
                           obligations=obligations, can_apply=ok, reason=reason, approved=approved, qr=qr,
                           doc_types=DOC_TYPES)


@bp.route("/apply", methods=["POST"])
@login_required("student")
def apply():
    try:
        workflow.submit_request(current_user()["user_id"])
        flash("Your clearance request has been submitted.", "success")
    except workflow.RuleError as exc:
        flash(str(exc), "danger")
    return redirect(url_for("student.dashboard"))


@bp.route("/resubmit/<int:step_id>", methods=["POST"])
@login_required("student")
def resubmit(step_id):
    try:
        workflow.resubmit(step_id, current_user()["user_id"])
        flash("Request returned to the unit for review.", "success")
    except PermissionError:
        abort(403)
    except workflow.RuleError as exc:
        flash(str(exc), "danger")
    return redirect(url_for("student.dashboard"))


@bp.route("/documents", methods=["POST"])
@login_required("student")
def upload():
    _user, student, req, *_ = _student_context()
    if req is None:
        flash("Submit a clearance request before uploading documents.", "danger")
        return redirect(url_for("student.dashboard"))
    f = request.files.get("file")
    doc_type = request.form.get("doc_type", "Other")
    if doc_type not in DOC_TYPES:
        doc_type = "Other"
    if f is None or not f.filename:
        flash("Choose a file to upload.", "danger")
        return redirect(url_for("student.dashboard"))
    name = secure_filename(f.filename)
    ext = name.rsplit(".", 1)[-1].lower() if "." in name else ""
    data = f.read(current_app.config["MAX_UPLOAD_BYTES"] + 1)
    if ext not in ALLOWED:
        flash("Only PDF, PNG and JPG files are accepted.", "danger")
    elif len(data) > current_app.config["MAX_UPLOAD_BYTES"]:
        flash("The file is larger than 2 MB.", "danger")
    elif not data.startswith(ALLOWED[ext]):
        flash("The file content does not match its extension.", "danger")
    else:
        stored = uuid.uuid4().hex + "." + ext
        with open(os.path.join(current_app.config["UPLOAD_DIR"], stored), "wb") as out:
            out.write(data)
        execute("INSERT INTO documents (request_id, file_path, original_name, doc_type, uploaded_at) VALUES (?, ?, ?, ?, ?)",
                (req["request_id"], stored, name, doc_type, now()))
        audit(current_user()["user_id"], "document_uploaded", name)
        flash("Document uploaded.", "success")
    return redirect(url_for("student.dashboard"))


@bp.route("/certificate.pdf")
@login_required("student")
def certificate_pdf():
    _user, _student, req, *_ = _student_context()
    if not req or req["overall_status"] != "cleared":
        abort(404)
    genuine = certificate.verify(req["certificate_no"])
    if not genuine:
        abort(404)
    pdf = certificate.certificate_pdf(genuine)
    return Response(pdf, mimetype="application/pdf",
                    headers={"Content-Disposition": 'attachment; filename="clearance-certificate.pdf"'})
