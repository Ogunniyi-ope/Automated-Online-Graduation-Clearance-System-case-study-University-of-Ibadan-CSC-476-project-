-- Automated Online Graduation Clearance System - database schema
-- Standard SQL (SQLite dialect; types and constraints are portable to MySQL).

CREATE TABLE IF NOT EXISTS clearance_units (
    unit_id      INTEGER PRIMARY KEY AUTOINCREMENT,
    unit_name    TEXT    NOT NULL UNIQUE,
    sequence_no  INTEGER NOT NULL UNIQUE,
    is_active    INTEGER NOT NULL DEFAULT 1 CHECK (is_active IN (0, 1))
);

CREATE TABLE IF NOT EXISTS users (
    user_id          INTEGER PRIMARY KEY AUTOINCREMENT,
    username         TEXT    NOT NULL UNIQUE,          -- matric number or staff ID
    password_hash    TEXT    NOT NULL,
    role             TEXT    NOT NULL CHECK (role IN ('student', 'officer', 'records', 'admin')),
    full_name        TEXT    NOT NULL,
    email            TEXT,
    phone            TEXT,
    unit_id          INTEGER REFERENCES clearance_units (unit_id),
    is_active        INTEGER NOT NULL DEFAULT 1 CHECK (is_active IN (0, 1)),
    failed_attempts  INTEGER NOT NULL DEFAULT 0,
    locked_until     TEXT,
    session_nonce    INTEGER NOT NULL DEFAULT 0   -- bumped on logout / password change: invalidates copied cookies
);

CREATE TABLE IF NOT EXISTS students (
    matric_no   TEXT    PRIMARY KEY,
    user_id     INTEGER NOT NULL UNIQUE REFERENCES users (user_id),
    faculty     TEXT    NOT NULL,
    department  TEXT    NOT NULL,
    level       INTEGER NOT NULL,
    session     TEXT    NOT NULL,
    hall        TEXT
);

CREATE TABLE IF NOT EXISTS clearance_requests (
    request_id      INTEGER PRIMARY KEY AUTOINCREMENT,
    matric_no       TEXT    NOT NULL UNIQUE REFERENCES students (matric_no),
    submitted_at    TEXT    NOT NULL,
    overall_status  TEXT    NOT NULL DEFAULT 'in_progress'
                    CHECK (overall_status IN ('in_progress', 'cleared')),
    certificate_no  TEXT    UNIQUE,
    cleared_at      TEXT
);

CREATE TABLE IF NOT EXISTS clearance_steps (
    step_id     INTEGER PRIMARY KEY AUTOINCREMENT,
    request_id  INTEGER NOT NULL REFERENCES clearance_requests (request_id),
    unit_id     INTEGER NOT NULL REFERENCES clearance_units (unit_id),
    status      TEXT    NOT NULL DEFAULT 'not_started'
                CHECK (status IN ('not_started', 'pending', 'approved', 'rejected')),
    officer_id  INTEGER REFERENCES users (user_id),
    remark      TEXT,
    opened_at   TEXT,
    decided_at  TEXT,
    UNIQUE (request_id, unit_id)
);

CREATE TABLE IF NOT EXISTS obligations (
    obligation_id  INTEGER PRIMARY KEY AUTOINCREMENT,
    matric_no      TEXT    NOT NULL REFERENCES students (matric_no),
    unit_id        INTEGER NOT NULL REFERENCES clearance_units (unit_id),
    description    TEXT    NOT NULL,
    amount         REAL    NOT NULL DEFAULT 0,
    is_cleared     INTEGER NOT NULL DEFAULT 0 CHECK (is_cleared IN (0, 1)),
    created_by     INTEGER REFERENCES users (user_id),
    created_at     TEXT    NOT NULL,
    cleared_at     TEXT
);

CREATE TABLE IF NOT EXISTS documents (
    doc_id       INTEGER PRIMARY KEY AUTOINCREMENT,
    request_id   INTEGER NOT NULL REFERENCES clearance_requests (request_id),
    file_path    TEXT    NOT NULL,      -- stored (random) name, outside the web root
    original_name TEXT   NOT NULL,
    doc_type     TEXT    NOT NULL,
    uploaded_at  TEXT    NOT NULL
);

CREATE TABLE IF NOT EXISTS notifications (
    notif_id  INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id   INTEGER NOT NULL REFERENCES users (user_id),
    message   TEXT    NOT NULL,
    channel   TEXT    NOT NULL CHECK (channel IN ('in_app', 'email', 'sms')),
    delivery  TEXT    NOT NULL DEFAULT 'recorded',   -- recorded | sent | simulated | failed
    sent_at   TEXT    NOT NULL,
    is_read   INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS audit_log (
    log_id      INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id     INTEGER REFERENCES users (user_id),
    action      TEXT NOT NULL,
    detail      TEXT,
    ip_address  TEXT,
    created_at  TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_steps_unit_status ON clearance_steps (unit_id, status);
CREATE INDEX IF NOT EXISTS idx_obl_student ON obligations (matric_no, unit_id, is_cleared);
CREATE INDEX IF NOT EXISTS idx_notif_user ON notifications (user_id, is_read);
