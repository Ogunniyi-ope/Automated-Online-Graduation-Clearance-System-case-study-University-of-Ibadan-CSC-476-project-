"""Administrator portal: users, units and order of approval, reports, audit log."""
import csv
import io

from flask import Blueprint, Response, abort, flash, redirect, render_template, request, url_for

from .auth import current_user, hash_password, login_required, password_problem
from .db import audit, execute, query, transaction

bp = Blueprint("admin", __name__, url_prefix="/admin")


@bp.route("/")
@login_required("admin", "records")
def dashboard():
    stats = {
        "students": query("SELECT COUNT(*) n FROM students", one=True)["n"],
        "requests": query("SELECT COUNT(*) n FROM clearance_requests", one=True)["n"],
        "cleared": query("SELECT COUNT(*) n FROM clearance_requests WHERE overall_status = 'cleared'", one=True)["n"],
        "pending": query("SELECT COUNT(*) n FROM clearance_steps WHERE status = 'pending'", one=True)["n"],
        "rejected": query("SELECT COUNT(*) n FROM clearance_steps WHERE status = 'rejected'", one=True)["n"],
    }
    per_unit = _per_unit()
    return render_template("admin_dashboard.html", stats=stats, per_unit=per_unit)


def _per_unit():
    return query(
        "SELECT cu.unit_name, cu.sequence_no, "
        "SUM(CASE WHEN st.status = 'approved' THEN 1 ELSE 0 END) AS approved, "
        "SUM(CASE WHEN st.status = 'pending' THEN 1 ELSE 0 END) AS pending, "
        "SUM(CASE WHEN st.status = 'rejected' THEN 1 ELSE 0 END) AS rejected, "
        "ROUND(AVG(CASE WHEN st.status = 'approved' THEN julianday(st.decided_at) - julianday(st.opened_at) END), 3) AS avg_days "
        "FROM clearance_units cu LEFT JOIN clearance_steps st ON st.unit_id = cu.unit_id "
        "GROUP BY cu.unit_id ORDER BY cu.sequence_no")


@bp.route("/reports.csv")
@login_required("admin", "records")
def report_csv():
    out = io.StringIO()
    w = csv.writer(out)
    w.writerow(["Matric no", "Name", "Faculty", "Department", "Request status", "Certificate no", "Cleared at"])
    for r in query("SELECT s.matric_no, u.full_name, s.faculty, s.department, "
                   "COALESCE(r.overall_status, 'not applied') st, r.certificate_no, r.cleared_at "
                   "FROM students s JOIN users u ON u.user_id = s.user_id "
                   "LEFT JOIN clearance_requests r ON r.matric_no = s.matric_no ORDER BY s.matric_no"):
        # neutralise spreadsheet formula injection
        w.writerow([("'" + str(v)) if isinstance(v, str) and v[:1] in "=+-@" else v for v in tuple(r)])
    return Response(out.getvalue(), mimetype="text/csv",
                    headers={"Content-Disposition": 'attachment; filename="clearance-report.csv"'})


@bp.route("/users", methods=["GET", "POST"])
@login_required("admin")
def users():
    me = current_user()
    if request.method == "POST":
        f = request.form
        role = f.get("role", "")
        username, full_name = f.get("username", "").strip(), f.get("full_name", "").strip()
        password = f.get("password", "")
        unit_id = f.get("unit_id") or None
        problem = password_problem(password)
        if role not in ("student", "officer", "records", "admin") or not username or not full_name:
            flash("Fill in the ID, name and role.", "danger")
        elif problem:
            flash(problem, "danger")
        elif role in ("officer", "records") and not unit_id:
            flash("Choose the unit for this officer.", "danger")
        elif query("SELECT 1 FROM users WHERE username = ?", (username,), one=True):
            flash("That ID already exists.", "danger")
        else:
            with transaction():
                uid = execute(
                    "INSERT INTO users (username, password_hash, role, full_name, email, phone, unit_id) VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (username, hash_password(password), role, full_name, f.get("email") or None, f.get("phone") or None,
                     int(unit_id) if unit_id and role in ("officer", "records") else None))
                if role == "student":
                    execute("INSERT INTO students (matric_no, user_id, faculty, department, level, session, hall) VALUES (?, ?, ?, ?, ?, ?, ?)",
                            (username, uid, f.get("faculty") or "Science", f.get("department") or "Computer Science",
                             int(f.get("level") or 400), f.get("session") or "2025/2026", f.get("hall") or None))
            audit(me["user_id"], "user_created", "%s (%s)" % (username, role))
            flash("User created.", "success")
        return redirect(url_for("admin.users"))
    rows = query("SELECT u.*, cu.unit_name FROM users u LEFT JOIN clearance_units cu ON cu.unit_id = u.unit_id ORDER BY u.role, u.username LIMIT 300")
    units = query("SELECT * FROM clearance_units ORDER BY sequence_no")
    return render_template("admin_users.html", rows=rows, units=units)


@bp.route("/users/<int:uid>/toggle", methods=["POST"])
@login_required("admin")
def toggle_user(uid):
    me = current_user()
    if uid == me["user_id"]:
        flash("You cannot deactivate your own account.", "danger")
    else:
        execute("UPDATE users SET is_active = 1 - is_active WHERE user_id = ?", (uid,))
        audit(me["user_id"], "user_toggled", str(uid))
    return redirect(url_for("admin.users"))


@bp.route("/units", methods=["GET", "POST"])
@login_required("admin")
def units():
    me = current_user()
    if request.method == "POST":
        action = request.form.get("action")
        try:
            if action == "add":
                name = request.form.get("unit_name", "").strip()
                seq = int(request.form.get("sequence_no", ""))
                if not name:
                    raise ValueError
                execute("INSERT INTO clearance_units (unit_name, sequence_no) VALUES (?, ?)", (name, seq))
            elif action == "update":
                uid = int(request.form["unit_id"])
                seq = int(request.form["sequence_no"])
                # swap with whichever unit currently holds that position, so numbers stay unique
                with transaction():
                    cur = query("SELECT sequence_no FROM clearance_units WHERE unit_id = ?", (uid,), one=True)
                    other = query("SELECT unit_id FROM clearance_units WHERE sequence_no = ? AND unit_id != ?", (seq, uid), one=True)
                    if cur is None:
                        raise ValueError
                    if other:
                        execute("UPDATE clearance_units SET sequence_no = -1 WHERE unit_id = ?", (uid,))
                        execute("UPDATE clearance_units SET sequence_no = ? WHERE unit_id = ?", (cur["sequence_no"], other["unit_id"]))
                    execute("UPDATE clearance_units SET sequence_no = ?, is_active = ? WHERE unit_id = ?",
                            (seq, 1 if request.form.get("is_active") else 0, uid))
            audit(me["user_id"], "unit_" + str(action), str(dict(request.form))[:200])
            flash("Units updated. New requests will follow the new order.", "success")
        except Exception:
            flash("Could not save the unit. Check the name and order number (it must be a whole number).", "danger")
        return redirect(url_for("admin.units"))
    return render_template("admin_units.html", units=query("SELECT * FROM clearance_units ORDER BY sequence_no"))


@bp.route("/audit")
@login_required("admin")
def audit_log():
    rows = query("SELECT a.*, u.username FROM audit_log a LEFT JOIN users u ON u.user_id = a.user_id ORDER BY a.log_id DESC LIMIT 200")
    return render_template("admin_audit.html", rows=rows)
