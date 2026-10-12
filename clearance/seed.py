"""Demo data: the eight clearance units, one officer per unit, an administrator and 30 fictional students.

All names, IDs, e-mail addresses and phone numbers below are invented for demonstration.
The shared demo password is meant for local testing only - change it before any real use.
"""
from .auth import hash_password
from .db import execute, now, query

DEMO_PASSWORD = "Demo@1234"

UNITS = [
    "Department", "Faculty", "University Library", "Bursary",
    "Hall of Residence", "Student Affairs", "University Health Services", "Examinations and Records",
]
STAFF = [  # username, full name, role, unit sequence
    ("staff.dept", "Dept. Clearance Officer", "officer", 1),
    ("staff.faculty", "Faculty Clearance Officer", "officer", 2),
    ("staff.library", "Library Clearance Officer", "officer", 3),
    ("staff.bursary", "Bursary Clearance Officer", "officer", 4),
    ("staff.hall", "Hall Clearance Officer", "officer", 5),
    ("staff.affairs", "Student Affairs Officer", "officer", 6),
    ("staff.health", "Health Services Officer", "officer", 7),
    ("staff.records", "Records Officer", "records", 8),
]
FIRST = ["Ade", "Chika", "Tunde", "Ngozi", "Musa", "Bisi", "Emeka", "Zainab", "Femi", "Amaka",
         "Ibrahim", "Kemi", "Segun", "Hauwa", "Obinna"]
LAST = ["Balogun", "Okafor", "Adeyemi", "Bello", "Eze", "Lawal", "Nwosu", "Abubakar", "Ojo", "Ibe"]
PROGRAMMES = [("Science", "Computer Science"), ("Science", "Physics"), ("Science", "Zoology"),
              ("Science", "Chemistry"), ("Arts", "English"), ("Social Sciences", "Economics")]
HALLS = ["Tedder Hall", "Sultan Bello Hall", "Queen Elizabeth II Hall", "Independence Hall", None]


def seed_demo():
    if query("SELECT 1 FROM clearance_units", one=True):
        return                                 # already seeded
    for i, name in enumerate(UNITS, start=1):
        execute("INSERT INTO clearance_units (unit_name, sequence_no) VALUES (?, ?)", (name, i))
    unit_id = {r["sequence_no"]: r["unit_id"] for r in query("SELECT unit_id, sequence_no FROM clearance_units")}
    pw = hash_password(DEMO_PASSWORD)
    execute("INSERT INTO users (username, password_hash, role, full_name, email) VALUES (?, ?, 'admin', ?, ?)",
            ("admin", pw, "System Administrator", "admin@example.edu.ng"))
    for username, name, role, seq in STAFF:
        execute("INSERT INTO users (username, password_hash, role, full_name, email, unit_id) VALUES (?, ?, ?, ?, ?, ?)",
                (username, pw, role, name, username + "@example.edu.ng", unit_id[seq]))
    for n in range(1, 31):
        matric = "25%04d" % n
        full = "%s %s" % (FIRST[(n * 7) % len(FIRST)], LAST[(n * 3) % len(LAST)])
        faculty, dept = PROGRAMMES[n % len(PROGRAMMES)]
        level = 300 if n in (29, 30) else 400            # two non-final-year students for the eligibility check
        uid = execute("INSERT INTO users (username, password_hash, role, full_name, email, phone) VALUES (?, ?, 'student', ?, ?, ?)",
                      (matric, pw, full, matric + "@students.example.edu.ng", "+23480000%05d" % n if n % 6 == 0 else None))
        execute("INSERT INTO students (matric_no, user_id, faculty, department, level, session, hall) VALUES (?, ?, ?, ?, ?, '2025/2026', ?)",
                (matric, uid, faculty, dept, level, HALLS[n % len(HALLS)]))
    ts = now()
    execute("INSERT INTO obligations (matric_no, unit_id, description, amount, created_at) VALUES (?, ?, ?, ?, ?)",
            ("250003", unit_id[3], "Unreturned library book: Introduction to Algorithms (overdue fine)", 1500, ts))
    execute("INSERT INTO obligations (matric_no, unit_id, description, amount, created_at) VALUES (?, ?, ?, ?, ?)",
            ("250005", unit_id[4], "Outstanding departmental levy", 45000, ts))
