"""Clearance workflow engine: eligibility, submission, routing, decisions, resubmission.

Rules (see report, Section 3.6.4):
  * only registered final-year students may apply, and only once;
  * the request is visible to a unit only when every earlier unit has approved it;
  * a rejection must carry a reason;
  * a unit cannot approve while the student has an open obligation recorded by that unit;
  * a rejected request returns to the same unit when the student resubmits;
  * when the last unit (Examinations and Records) approves, the certificate is issued.
"""
from flask import current_app

from . import certificate
from .db import audit, execute, now, query, transaction
from .notify import notify, notify_unit


class RuleError(Exception):
    """A business rule prevented the action; the message is safe to show to the user."""


def get_student(user_id):
    return query(
        "SELECT s.*, u.full_name FROM students s JOIN users u ON u.user_id = s.user_id WHERE s.user_id = ?",
        (user_id,), one=True)


def eligibility(student):
    """Return (ok, reason)."""
    if student is None:
        return False, "No student record is attached to this account."
    if student["level"] < current_app.config["FINAL_YEAR_LEVEL"]:
        return False, "Only final-year students (%d level) may apply for graduation clearance." % current_app.config["FINAL_YEAR_LEVEL"]
    if query("SELECT 1 FROM clearance_requests WHERE matric_no = ?", (student["matric_no"],), one=True):
        return False, "You have already submitted a clearance request."
    if not query("SELECT 1 FROM clearance_units WHERE is_active = 1", one=True):
        return False, "No clearance unit is configured. Please contact the administrator."
    return True, ""


def submit_request(user_id):
    student = get_student(user_id)
    ok, reason = eligibility(student)
    if not ok:
        raise RuleError(reason)
    with transaction():
        ts = now()
        req_id = execute(
            "INSERT INTO clearance_requests (matric_no, submitted_at) VALUES (?, ?)", (student["matric_no"], ts))
        units = query("SELECT unit_id FROM clearance_units WHERE is_active = 1 ORDER BY sequence_no")
        for i, u in enumerate(units):
            execute(
                "INSERT INTO clearance_steps (request_id, unit_id, status, opened_at) VALUES (?, ?, ?, ?)",
                (req_id, u["unit_id"], "pending" if i == 0 else "not_started", ts if i == 0 else None))
        audit(user_id, "request_submitted", "request %d" % req_id)
        first = units[0]["unit_id"]
    notify(user_id, "Your graduation clearance request has been submitted and sent to the first unit.")
    notify_unit(first, "A new clearance request from %s (%s) is awaiting your action." % (student["full_name"], student["matric_no"]))
    return req_id


def open_obligations(matric_no, unit_id):
    return query(
        "SELECT * FROM obligations WHERE matric_no = ? AND unit_id = ? AND is_cleared = 0", (matric_no, unit_id))


def decide(step_id, officer, decision, remark=""):
    """Record an officer's decision on a pending step. Returns the request row."""
    remark = (remark or "").strip()
    if decision not in ("approved", "rejected"):
        raise RuleError("Unknown decision.")
    if decision == "rejected" and not remark:
        raise RuleError("A reason is required when rejecting a request.")
    if len(remark) > 500:
        raise RuleError("The remark is too long (maximum 500 characters).")
    notices = []
    with transaction():
        step = query(
            "SELECT st.*, r.matric_no, r.request_id AS rid FROM clearance_steps st "
            "JOIN clearance_requests r ON r.request_id = st.request_id WHERE st.step_id = ?",
            (step_id,), one=True)
        if step is None:
            raise RuleError("Request not found.")
        if officer["unit_id"] != step["unit_id"]:
            raise PermissionError("This request belongs to another unit.")
        if step["status"] != "pending":
            raise RuleError("This request has already been decided or is not yet open for your unit.")
        if decision == "approved":
            owed = open_obligations(step["matric_no"], step["unit_id"])
            if owed:
                raise RuleError("The student still has %d outstanding obligation(s) at this unit. "
                                "Clear them or reject the request with a reason." % len(owed))
        ts = now()
        execute("UPDATE clearance_steps SET status = ?, officer_id = ?, remark = ?, decided_at = ? WHERE step_id = ?",
                (decision, officer["user_id"], remark or None, ts, step_id))
        student_user = query("SELECT s.user_id, u.full_name FROM students s JOIN users u ON u.user_id = s.user_id "
                             "WHERE s.matric_no = ?", (step["matric_no"],), one=True)
        unit = query("SELECT unit_name FROM clearance_units WHERE unit_id = ?", (step["unit_id"],), one=True)
        audit(officer["user_id"], "step_" + decision, "request %d, unit %s" % (step["rid"], unit["unit_name"]))
        if decision == "rejected":
            notices.append(("user", student_user["user_id"],
                            "%s rejected your clearance request. Reason: %s" % (unit["unit_name"], remark)))
        else:
            nxt = query(
                "SELECT st.step_id, st.unit_id FROM clearance_steps st "
                "JOIN clearance_units cu ON cu.unit_id = st.unit_id "
                "WHERE st.request_id = ? AND st.status = 'not_started' ORDER BY cu.sequence_no LIMIT 1",
                (step["rid"],), one=True)
            if nxt:
                execute("UPDATE clearance_steps SET status = 'pending', opened_at = ? WHERE step_id = ?", (ts, nxt["step_id"]))
                notices.append(("user", student_user["user_id"], "%s has cleared you. Your request has moved to the next unit." % unit["unit_name"]))
                notices.append(("unit", nxt["unit_id"], "A clearance request from %s (%s) is awaiting your action." % (student_user["full_name"], step["matric_no"])))
            else:
                number = certificate.issue(step["rid"], step["matric_no"])
                execute("UPDATE clearance_requests SET overall_status = 'cleared', certificate_no = ?, cleared_at = ? WHERE request_id = ?",
                        (number, ts, step["rid"]))
                audit(officer["user_id"], "certificate_issued", number)
                notices.append(("user", student_user["user_id"],
                                "Congratulations. You have been cleared by all units. Certificate %s is ready to download." % number))
    for kind, target, msg in notices:
        (notify if kind == "user" else notify_unit)(target, msg)
    return query("SELECT * FROM clearance_requests WHERE request_id = ?", (step["rid"],), one=True)


def resubmit(step_id, student_user_id):
    """The student resolved the problem: the rejected step returns to 'pending' at the same unit."""
    student = get_student(student_user_id)
    with transaction():
        step = query(
            "SELECT st.*, r.matric_no FROM clearance_steps st JOIN clearance_requests r ON r.request_id = st.request_id "
            "WHERE st.step_id = ?", (step_id,), one=True)
        if step is None or student is None or step["matric_no"] != student["matric_no"]:
            raise PermissionError("Not your request.")
        if step["status"] != "rejected":
            raise RuleError("Only a rejected step can be resubmitted.")
        execute("UPDATE clearance_steps SET status = 'pending', opened_at = ?, decided_at = NULL WHERE step_id = ?",
                (now(), step_id))
        audit(student_user_id, "step_resubmitted", "step %d" % step_id)
        unit_id = step["unit_id"]
    notify_unit(unit_id, "%s (%s) has resubmitted a clearance request for your review." % (student["full_name"], student["matric_no"]))
    notify(student_user_id, "Your request has been returned to the unit for review.")


def add_obligation(officer, matric_no, description, amount):
    description = (description or "").strip()
    if not description:
        raise RuleError("Describe the obligation.")
    if not query("SELECT 1 FROM students WHERE matric_no = ?", (matric_no,), one=True):
        raise RuleError("No student with that matriculation number.")
    try:
        amount = float(amount or 0)
    except ValueError:
        raise RuleError("Amount must be a number.")
    if amount < 0:
        raise RuleError("Amount cannot be negative.")
    oid = execute(
        "INSERT INTO obligations (matric_no, unit_id, description, amount, created_by, created_at) VALUES (?, ?, ?, ?, ?, ?)",
        (matric_no, officer["unit_id"], description, amount, officer["user_id"], now()))
    audit(officer["user_id"], "obligation_added", "%s: %s" % (matric_no, description))
    return oid


def clear_obligation(officer, obligation_id):
    row = query("SELECT * FROM obligations WHERE obligation_id = ?", (obligation_id,), one=True)
    if row is None:
        raise RuleError("Obligation not found.")
    if row["unit_id"] != officer["unit_id"]:
        raise PermissionError("This obligation belongs to another unit.")
    execute("UPDATE obligations SET is_cleared = 1, cleared_at = ? WHERE obligation_id = ?", (now(), obligation_id))
    audit(officer["user_id"], "obligation_cleared", "%s: %s" % (row["matric_no"], row["description"]))
