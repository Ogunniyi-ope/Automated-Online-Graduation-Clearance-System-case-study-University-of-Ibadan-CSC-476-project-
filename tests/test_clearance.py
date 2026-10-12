"""Automated tests. Test names carry the IDs used in the project report (Tables 4.1 - 4.3).

Run:  python -m unittest discover -s tests -v
"""
import io
import os
import re
import shutil
import tempfile
import threading
import time
import unittest

from clearance import create_app, certificate
from clearance.db import init_db, query
from clearance.seed import seed_demo, DEMO_PASSWORD

UNIT_STAFF = ["staff.dept", "staff.faculty", "staff.library", "staff.bursary",
              "staff.hall", "staff.affairs", "staff.health", "staff.records"]
PDF = b"%PDF-1.4\n1 0 obj<<>>endobj\ntrailer<<>>\n%%EOF"


class Base(unittest.TestCase):
    config = {}

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        cfg = dict(TESTING=True, DATABASE=os.path.join(self.tmp, "t.sqlite"),
                   UPLOAD_DIR=os.path.join(self.tmp, "up"), SECRET_KEY="test", CERT_KEY="test-cert")
        cfg.update(self.config)
        self.app = create_app(cfg)
        os.makedirs(cfg["UPLOAD_DIR"], exist_ok=True)
        with self.app.app_context():
            init_db()
            seed_demo()

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    # --- helpers
    def client(self, username=None, password=DEMO_PASSWORD):
        c = self.app.test_client()
        c.get("/login")                      # starts a session with a CSRF token
        if username:
            r = self.post(c, "/login", username=username, password=password)
            self.assertEqual(r.status_code, 302, "login failed for " + username)
        return c

    def tok(self, c):
        with c.session_transaction() as s:
            return s.get("csrf", "")

    def post(self, c, url, files=None, follow_redirects=False, **data):
        data["csrf_token"] = self.tok(c)
        if files:
            data.update(files)
            return c.post(url, data=data, content_type="multipart/form-data", follow_redirects=follow_redirects)
        return c.post(url, data=data, follow_redirects=follow_redirects)

    def q(self, sql, args=()):
        with self.app.app_context():
            return [dict(r) for r in query(sql, args)]

    def apply(self, matric="250001"):
        c = self.client(matric)
        self.post(c, "/student/apply")
        return c

    def pending_step(self, officer_client):
        html = officer_client.get("/officer/").get_data(as_text=True)
        m = re.search(r"/officer/request/(\d+)", html)
        return int(m.group(1)) if m else None

    def decide(self, officer_client, step_id, decision="approve", remark=""):
        return self.post(officer_client, "/officer/decide/%d" % step_id, decision=decision, remark=remark)

    def run_through(self, matric="250001", upto=8):
        """Student applies and units 1..upto approve in turn. Returns the student client."""
        sc = self.apply(matric)
        for staff in UNIT_STAFF[:upto]:
            oc = self.client(staff)
            sid = self.pending_step(oc)
            self.assertIsNotNone(sid, "no pending step for " + staff)
            self.decide(oc, sid, "approve")
        return sc


class TestAuthentication(Base):
    def test_T01_login_valid_credentials_opens_role_dashboard(self):
        homes = {"250001": "/student/", "staff.dept": "/officer/", "staff.records": "/officer/", "admin": "/admin/"}
        for user, home in homes.items():
            c = self.app.test_client(); c.get("/login")
            r = self.post(c, "/login", username=user, password=DEMO_PASSWORD)
            self.assertEqual((r.status_code, r.headers["Location"]), (302, home), user)

    def test_T02_wrong_password_denied_with_generic_message(self):
        c = self.client()
        r = self.post(c, "/login", username="250001", password="wrong")
        self.assertEqual(r.status_code, 401)
        self.assertIn("Invalid ID or password", r.get_data(as_text=True))
        r2 = self.post(c, "/login", username="no.such.user", password="wrong")   # same message: no user enumeration
        self.assertIn("Invalid ID or password", r2.get_data(as_text=True))

    def test_SEC10_account_locks_after_five_failures(self):
        c = self.client()
        for _ in range(5):
            self.post(c, "/login", username="250002", password="bad")
        r = self.post(c, "/login", username="250002", password=DEMO_PASSWORD)    # correct password, but locked
        self.assertEqual(r.status_code, 429)

    def test_SEC7_passwords_are_stored_hashed(self):
        rows = self.q("SELECT password_hash FROM users")
        self.assertTrue(all(r["password_hash"].startswith(("scrypt:", "pbkdf2:")) for r in rows))
        self.assertTrue(all(DEMO_PASSWORD not in r["password_hash"] for r in rows))


class TestWorkflow(Base):
    def test_T03_non_final_year_student_cannot_apply(self):
        c = self.client("250029")                                   # 300 level
        r = self.post(c, "/student/apply", follow_redirects=True)
        self.assertIn("final-year", r.get_data(as_text=True))
        self.assertEqual(self.q("SELECT * FROM clearance_requests WHERE matric_no='250029'"), [])

    def test_T04_eligible_student_creates_request_routed_to_first_unit(self):
        self.apply("250001")
        steps = self.q("SELECT cu.sequence_no, st.status FROM clearance_steps st JOIN clearance_units cu "
                       "ON cu.unit_id = st.unit_id JOIN clearance_requests r ON r.request_id = st.request_id "
                       "WHERE r.matric_no = '250001' ORDER BY cu.sequence_no")
        self.assertEqual(len(steps), 8)
        self.assertEqual([s["status"] for s in steps], ["pending"] + ["not_started"] * 7)
        self.assertIsNotNone(self.pending_step(self.client("staff.dept")))
        self.assertIsNone(self.pending_step(self.client("staff.faculty")))   # not visible yet

    def test_T04b_cannot_apply_twice(self):
        c = self.apply("250001")
        self.post(c, "/student/apply")
        self.assertEqual(len(self.q("SELECT * FROM clearance_requests WHERE matric_no='250001'")), 1)

    def test_T05_approval_moves_request_to_next_unit(self):
        self.apply("250001")
        oc = self.client("staff.dept")
        self.decide(oc, self.pending_step(oc), "approve")
        self.assertIsNotNone(self.pending_step(self.client("staff.faculty")))
        self.assertIsNone(self.pending_step(oc))

    def test_T06_rejection_without_reason_is_refused(self):
        self.apply("250001")
        oc = self.client("staff.dept")
        sid = self.pending_step(oc)
        r = self.decide(oc, sid, "reject", remark="   ")
        r = oc.get("/officer/request/%d" % sid)
        self.assertIn("reason is required", r.get_data(as_text=True))
        self.assertEqual(self.q("SELECT status FROM clearance_steps WHERE step_id=?", (sid,))[0]["status"], "pending")

    def test_T07_rejected_student_resubmits_to_same_unit(self):
        sc = self.apply("250001")
        oc = self.client("staff.dept")
        sid = self.pending_step(oc)
        self.decide(oc, sid, "reject", "Submit your final project report.")
        page = sc.get("/student/").get_data(as_text=True)
        self.assertIn("Submit your final project report.", page)
        self.post(sc, "/student/resubmit/%d" % sid)
        self.assertEqual(self.q("SELECT status FROM clearance_steps WHERE step_id=?", (sid,))[0]["status"], "pending")
        self.assertEqual(self.pending_step(self.client("staff.dept")), sid)

    def test_T08_upload_validation(self):
        sc = self.apply("250001")
        up = lambda name, data: self.post(sc, "/student/documents", doc_type="Payment receipt",
                                          files={"file": (io.BytesIO(data), name)}, follow_redirects=False)
        up("virus.exe", b"MZ\x90\x00")
        up("fake.pdf", b"this is not a pdf")
        big = up("big.pdf", PDF + b"0" * (2 * 1024 * 1024 + 10))
        self.assertEqual(len(self.q("SELECT * FROM documents")), 0)
        up("receipt.pdf", PDF)
        docs = self.q("SELECT * FROM documents")
        self.assertEqual(len(docs), 1)
        self.assertNotEqual(docs[0]["file_path"], "receipt.pdf")           # stored under a random name

    def test_T09_administrator_can_change_order_of_units(self):
        ac = self.client("admin")
        units = {u["unit_name"]: u for u in self.q("SELECT * FROM clearance_units")}
        lib, bur = units["University Library"], units["Bursary"]
        self.apply("250010")                                                # old order, request already open
        self.post(ac, "/admin/units", action="update", unit_id=lib["unit_id"], sequence_no=bur["sequence_no"], is_active="on")
        self.apply("250011")                                                # new request, new order
        order = lambda m: [r["unit_name"] for r in self.q(
            "SELECT cu.unit_name FROM clearance_steps st JOIN clearance_units cu ON cu.unit_id = st.unit_id "
            "JOIN clearance_requests r ON r.request_id = st.request_id WHERE r.matric_no = ? ORDER BY st.step_id", (m,))]
        self.assertEqual(order("250010")[2:4], ["University Library", "Bursary"])    # unchanged
        # creation order of steps follows sequence at the time, so compare by sequence of activation:
        seq = self.q("SELECT unit_name FROM clearance_units ORDER BY sequence_no")
        names = [s["unit_name"] for s in seq]
        self.assertEqual(names[2:4], ["Bursary", "University Library"])
        self.assertEqual(order("250011")[2:4], ["Bursary", "University Library"])

    def test_T10_certificate_number_and_pdf_after_final_approval(self):
        sc = self.run_through("250001")
        req = self.q("SELECT * FROM clearance_requests WHERE matric_no='250001'")[0]
        self.assertEqual(req["overall_status"], "cleared")
        self.assertRegex(req["certificate_no"], r"^UI/CLR/\d{4}/\d{6}-[0-9A-F]{8}$")
        pdf = sc.get("/student/certificate.pdf")
        self.assertEqual((pdf.status_code, pdf.data[:4]), (200, b"%PDF"))


class TestSystemScenarios(Base):
    def test_S1_student_without_obligations_gets_certificate(self):
        sc = self.run_through("250001")
        page = sc.get("/student/").get_data(as_text=True)
        self.assertIn("Graduation Clearance Certificate", page)
        self.assertIn("<svg", page)                                       # QR code shown
        self.assertEqual(len(self.q("SELECT * FROM clearance_steps WHERE status='approved'")), 8)

    def test_S2_library_obligation_blocks_then_clears(self):
        sc = self.apply("250003")
        for staff in UNIT_STAFF[:2]:
            oc = self.client(staff); self.decide(oc, self.pending_step(oc), "approve")
        lib = self.client("staff.library")
        sid = self.pending_step(lib)
        self.decide(lib, sid, "approve")                                  # blocked: open obligation
        self.assertEqual(self.q("SELECT status FROM clearance_steps WHERE step_id=?", (sid,))[0]["status"], "pending")
        self.assertIn("outstanding obligation", lib.get("/officer/request/%d" % sid).get_data(as_text=True))
        self.decide(lib, sid, "reject", "Return the overdue book and pay the fine.")
        self.post(sc, "/student/resubmit/%d" % sid)
        oid = self.q("SELECT obligation_id FROM obligations WHERE matric_no='250003'")[0]["obligation_id"]
        self.post(lib, "/officer/obligations/%d/clear" % oid, back="/officer/")
        self.decide(lib, sid, "approve")
        self.assertEqual(self.q("SELECT status FROM clearance_steps WHERE step_id=?", (sid,))[0]["status"], "approved")

    def test_S3_simultaneous_decisions_only_one_succeeds(self):
        self.apply("250001")
        with self.app.app_context():
            from clearance.auth import hash_password
            from clearance.db import execute
            unit = query("SELECT unit_id FROM clearance_units WHERE sequence_no = 1", one=True)["unit_id"]
            execute("INSERT INTO users (username, password_hash, role, full_name, unit_id) VALUES ('staff.dept2', ?, 'officer', 'Second Dept Officer', ?)",
                    (hash_password(DEMO_PASSWORD), unit))
        a, b = self.client("staff.dept"), self.client("staff.dept2")
        sid = self.pending_step(a)
        out = []
        def go(c):
            r = self.decide(c, sid, "approve")
            out.append(r.status_code)
        ts = [threading.Thread(target=go, args=(c,)) for c in (a, b)]
        [t.start() for t in ts]; [t.join() for t in ts]
        self.assertEqual(self.q("SELECT COUNT(*) n FROM clearance_steps WHERE status='approved'")[0]["n"], 1)
        self.assertEqual(self.q("SELECT COUNT(*) n FROM clearance_steps WHERE status='pending'")[0]["n"], 1)   # next unit opened once
        self.assertEqual(self.q("SELECT COUNT(*) n FROM audit_log WHERE action='step_approved'")[0]["n"], 1)

    def test_S4_users_cannot_open_other_users_or_roles_pages(self):
        sc = self.apply("250001")
        self.assertEqual(sc.get("/officer/").status_code, 403)
        self.assertEqual(sc.get("/admin/").status_code, 403)
        self.assertEqual(sc.get("/admin/users").status_code, 403)
        self.assertEqual(sc.get("/student/certificate.pdf").status_code, 404)          # not cleared
        fac = self.client("staff.faculty")
        sid = self.q("SELECT step_id FROM clearance_steps WHERE status='pending'")[0]["step_id"]
        self.assertEqual(fac.get("/officer/request/%d" % sid).status_code, 403)       # another unit's request
        self.assertEqual(fac.get("/admin/").status_code, 403)
        anon = self.app.test_client()
        self.assertEqual(anon.get("/student/").status_code, 302)                        # redirected to login

    def test_S5_certificate_verification_genuine_and_altered(self):
        self.run_through("250001")
        no = self.q("SELECT certificate_no FROM clearance_requests WHERE matric_no='250001'")[0]["certificate_no"]
        anon = self.app.test_client()
        self.assertIn("GENUINE", anon.get("/verify", query_string={"no": no}).get_data(as_text=True))
        bad_sig = no[:-1] + ("0" if no[-1] != "0" else "1")
        bad_id = re.sub(r"/(\d{6})-", "/000999-", no)
        bad_year = re.sub(r"/20\d\d/", "/1999/", no)
        for bad in (bad_sig, bad_id, bad_year, "garbage"):
            self.assertIn("NOT VERIFIED", anon.get("/verify", query_string={"no": bad}).get_data(as_text=True), bad)
        with self.app.app_context():
            self.assertIsNone(certificate.verify(bad_sig))

    def test_S6_notifications_are_recorded(self):
        sc = self.apply("250001")
        oc = self.client("staff.dept")
        self.decide(oc, self.pending_step(oc), "reject", "Missing signature on project.")
        msgs = self.q("SELECT channel, message FROM notifications n JOIN users u ON u.user_id = n.user_id WHERE u.username='250001'")
        self.assertTrue({"in_app", "email"} <= {m["channel"] for m in msgs})
        self.assertTrue(any("Missing signature on project." in m["message"] for m in msgs))
        self.assertTrue(self.q("SELECT 1 FROM notifications n JOIN users u ON u.user_id = n.user_id WHERE u.username='staff.dept'"))
        self.assertIn("Missing signature", sc.get("/notifications").get_data(as_text=True))

    def test_S7_reports_and_csv_export(self):
        self.run_through("250001")
        ac, rc, sc = self.client("admin"), self.client("staff.records"), self.client("250002")
        page = ac.get("/admin/").get_data(as_text=True)
        self.assertIn("Fully cleared", page)
        self.assertEqual(rc.get("/admin/").status_code, 200)                       # records officer may view reports
        self.assertEqual(sc.get("/admin/reports.csv").status_code, 403)
        csv = ac.get("/admin/reports.csv")
        self.assertEqual(csv.mimetype, "text/csv")
        self.assertIn("250001", csv.get_data(as_text=True))
        self.assertIn("250001", rc.get("/officer/cleared").get_data(as_text=True))

    def test_S8_layout_is_responsive(self):
        html = self.app.test_client().get("/login").get_data(as_text=True)
        self.assertIn('name="viewport"', html)
        css = self.app.test_client().get("/static/style.css").get_data(as_text=True)
        self.assertIn("@media (max-width:640px)", css)


class TestSecurity(Base):
    def test_SEC1_role_based_access_control(self):
        oc = self.client("staff.dept")
        for url in ("/admin/users", "/admin/units", "/admin/audit", "/student/"):
            self.assertEqual(oc.get(url).status_code, 403, url)
        self.assertEqual(self.client("staff.dept").get("/officer/cleared").status_code, 403)   # records only

    def test_SEC2_sql_injection_has_no_effect(self):
        c = self.client()
        r = self.post(c, "/login", username="' OR '1'='1' --", password="x")
        self.assertEqual(r.status_code, 401)
        oc = self.client("staff.dept")
        r = oc.get("/officer/students", query_string={"q": "' OR 1=1 --"})
        self.assertEqual(r.status_code, 200)
        self.assertNotIn("250001", r.get_data(as_text=True))
        self.assertEqual(self.q("SELECT COUNT(*) n FROM users")[0]["n"], 39)

    def test_SEC3_stored_xss_is_escaped(self):
        sc = self.apply("250001")
        oc = self.client("staff.dept")
        payload = "<script>alert(1)</script>"
        self.decide(oc, self.pending_step(oc), "reject", payload)
        page = sc.get("/student/").get_data(as_text=True)
        self.assertNotIn(payload, page)
        self.assertIn("&lt;script&gt;alert(1)&lt;/script&gt;", page)

    def test_SEC4_csrf_token_required(self):
        c = self.client("250001")
        self.assertEqual(c.post("/student/apply", data={}).status_code, 400)
        self.assertEqual(c.post("/student/apply", data={"csrf_token": "forged"}).status_code, 400)
        self.assertEqual(self.q("SELECT * FROM clearance_requests"), [])

    def test_SEC5_session_invalid_after_logout(self):
        c = self.client("250001")
        old = c.get_cookie("session").value
        self.post(c, "/logout")
        self.assertEqual(c.get("/student/").status_code, 302)
        replay = self.app.test_client()
        replay.set_cookie("session", old)
        self.assertEqual(replay.get("/student/").status_code, 302)           # copied cookie no longer works

    def test_SEC6_unrestricted_upload_blocked(self):
        sc = self.apply("250001")
        for name, data in [("shell.php", b"<?php system($_GET['c']); ?>"), ("run.exe", b"MZ"),
                           ("../../evil.pdf", b"not a pdf"), ("huge.png", b"\x89PNG\r\n\x1a\n" + b"0" * 3_500_000)]:
            r = self.post(sc, "/student/documents", doc_type="Other", files={"file": (io.BytesIO(data), name)})
            self.assertIn(r.status_code, (302, 413))
        self.assertEqual(os.listdir(self.app.config["UPLOAD_DIR"]), [])

    def test_SEC8_security_headers(self):
        r = self.app.test_client().get("/login")
        for h in ("Content-Security-Policy", "X-Frame-Options", "X-Content-Type-Options"):
            self.assertIn(h, r.headers)

    def test_SEC9_no_open_redirect(self):
        oc = self.client("staff.dept")
        r = self.post(oc, "/officer/obligations", matric_no="250001", description="x", amount="0", back="https://evil.example/")
        self.assertEqual(r.headers["Location"], "/officer/")

    def test_SEC11_officer_cannot_clear_other_units_obligation(self):
        oid = self.q("SELECT obligation_id FROM obligations WHERE matric_no='250003'")[0]["obligation_id"]   # library obligation
        bur = self.client("staff.bursary")
        self.assertEqual(self.post(bur, "/officer/obligations/%d/clear" % oid).status_code, 403)

    def test_SEC12_decision_on_another_units_step_is_forbidden(self):
        self.apply("250001")
        sid = self.q("SELECT step_id FROM clearance_steps WHERE status='pending'")[0]["step_id"]
        self.assertEqual(self.decide(self.client("staff.library"), sid, "approve").status_code, 403)
        self.assertEqual(self.q("SELECT status FROM clearance_steps WHERE step_id=?", (sid,))[0]["status"], "pending")


class TestSessionTimeout(Base):
    config = {"PERMANENT_SESSION_LIFETIME": __import__("datetime").timedelta(seconds=1)}

    def test_SEC5b_session_expires_after_inactivity(self):
        c = self.client("250001")
        self.assertEqual(c.get("/student/").status_code, 200)
        time.sleep(2.2)
        self.assertEqual(c.get("/student/").status_code, 302)


if __name__ == "__main__":
    unittest.main(verbosity=2)
