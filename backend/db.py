"""Shared SQLite schema + helpers for the attendance system."""
import sqlite3
from pathlib import Path

DB_PATH = Path(__file__).resolve().parent.parent / "data" / "activity.db"

# Two people can share a first name, and two can share a full name, so identity
# lives in the row id. This expression is only for display and search.
FULL_NAME_SQL = "TRIM(e.first_name || ' ' || e.last_name)"


# WARNING on the `DEFAULT (datetime('now'))` columns below: SQLite writes those
# as "2026-10-27 13:00:00" while every timestamp this code inserts is
# "2026-10-27T13:00:00+00:00". The two do not compare as strings — a space sorts
# below "T" — so a column holding both will silently fail any "since" query.
# Always pass such a timestamp explicitly from Python (config.now_utc()) when
# the column will be compared, and read it back through config.parse_ts.


def get_conn():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    # Several processes share this file: WAL lets a reader and a writer work at
    # once, and the timeout waits a lock out instead of raising.
    con = sqlite3.connect(DB_PATH, timeout=30)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("PRAGMA busy_timeout=30000")
    return con


def _migrate_employees(con):
    """Move from a single UNIQUE `name` to first/last/position.

    The old UNIQUE(name) made two employees with the same name impossible, which
    a real company hits quickly. Rebuilding is the only way to drop it in SQLite.
    """
    cols = {r["name"] for r in con.execute("PRAGMA table_info(employees)")}
    if not cols or "first_name" in cols:
        return

    con.executescript("""
        CREATE TABLE employees_new (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            first_name TEXT NOT NULL,
            last_name TEXT NOT NULL DEFAULT '',
            position TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL DEFAULT (datetime('now'))
        );
        INSERT INTO employees_new (id, first_name, last_name, created_at)
            SELECT id,
                   CASE WHEN instr(name, ' ') > 0
                        THEN substr(name, 1, instr(name, ' ') - 1) ELSE name END,
                   CASE WHEN instr(name, ' ') > 0
                        THEN substr(name, instr(name, ' ') + 1) ELSE '' END,
                   created_at
            FROM employees;
        DROP TABLE employees;
        ALTER TABLE employees_new RENAME TO employees;
    """)
    con.commit()
    print("[db] таблица employees переведена на имя/фамилию")


def init_db():
    con = get_conn()
    con.executescript("""
        CREATE TABLE IF NOT EXISTS employees (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            first_name TEXT NOT NULL,
            last_name TEXT NOT NULL DEFAULT '',
            position TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL DEFAULT (datetime('now'))
        );

        -- One employee has several reference faces (different angles/lighting).
        -- `embedding` is a raw float32 vector straight from the recognition model.
        CREATE TABLE IF NOT EXISTS face_embeddings (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            employee_id INTEGER NOT NULL,
            embedding BLOB NOT NULL,
            source_image TEXT,
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            FOREIGN KEY (employee_id) REFERENCES employees(id)
        );

        -- Superseded by attendance_events, which records how each arrival and
        -- departure was established. Kept so the rows written before the change
        -- are not lost; nothing writes here any more.
        CREATE TABLE IF NOT EXISTS presence_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            employee_id INTEGER NOT NULL,
            ts TEXT NOT NULL,
            event TEXT NOT NULL,
            FOREIGN KEY (employee_id) REFERENCES employees(id)
        );

        -- What the day looked like: one row each time a known face is seen, so a
        -- timeline shows real gaps instead of guessing between two events.
        CREATE TABLE IF NOT EXISTS presence_samples (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            employee_id INTEGER NOT NULL,
            ts TEXT NOT NULL,
            FOREIGN KEY (employee_id) REFERENCES employees(id)
        );

        -- The attendance record proper. `source` is kept because the three ways
        -- a row can appear carry different weight in a dispute: the employee
        -- stood at the camera, they only walked past, or the system closed the
        -- day for them. `local_day` is the office date, so a query for "today"
        -- never has to reason about UTC offsets.
        CREATE TABLE IF NOT EXISTS attendance_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            employee_id INTEGER NOT NULL,
            ts TEXT NOT NULL,
            local_day TEXT NOT NULL,
            event TEXT NOT NULL,              -- 'keldi' | 'ketdi'
            source TEXT NOT NULL,             -- 'deliberate' | 'first_sighting'
                                              -- | 'auto_close' | 'manual'
            confirmed TEXT,                   -- NULL | 'ha' | 'yoq' | 'sabab'
            reason TEXT,
            confirmed_at TEXT,
            cancelled INTEGER NOT NULL DEFAULT 0,
            suspicious INTEGER NOT NULL DEFAULT 0,
            note TEXT,
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            FOREIGN KEY (employee_id) REFERENCES employees(id)
        );

        -- Which Telegram account belongs to which employee. One account can
        -- stand for only one person, so a shared phone cannot clock two people.
        CREATE TABLE IF NOT EXISTS telegram_links (
            employee_id INTEGER PRIMARY KEY,
            telegram_id INTEGER NOT NULL UNIQUE,
            username TEXT,
            linked_at TEXT NOT NULL DEFAULT (datetime('now')),
            FOREIGN KEY (employee_id) REFERENCES employees(id)
        );

        -- One-time codes the admin hands out. Kept after use rather than
        -- deleted, so "who linked this account, and when" stays answerable.
        CREATE TABLE IF NOT EXISTS pairing_codes (
            code TEXT PRIMARY KEY,
            employee_id INTEGER NOT NULL,
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            expires_at TEXT NOT NULL,
            used_at TEXT,
            used_by INTEGER,
            FOREIGN KEY (employee_id) REFERENCES employees(id)
        );

        -- Wrong-code attempts, so a 8-character code cannot be brute forced.
        CREATE TABLE IF NOT EXISTS pairing_attempts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            telegram_id INTEGER NOT NULL,
            ts TEXT NOT NULL DEFAULT (datetime('now'))
        );

        -- Every message the system decides to send. A row is written whether or
        -- not sending is switched on, so while Telegram is off the outbox shows
        -- exactly what would have gone out, and nothing is lost when it is
        -- switched on later.
        CREATE TABLE IF NOT EXISTS telegram_outbox (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            employee_id INTEGER,
            telegram_id INTEGER,
            event_id INTEGER,
            kind TEXT NOT NULL,
            body TEXT NOT NULL,
            buttons TEXT,
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            sent_at TEXT,
            error TEXT
        );

        -- People crossing the door line. Deliberately holds no employee_id:
        -- this counts everyone who walks through, most of whom — visitors,
        -- students — are not in the system and never will be. Tying it to
        -- attendance would invite the two to be compared as if they should
        -- match, and they never will.
        CREATE TABLE IF NOT EXISTS crossings (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ts TEXT NOT NULL,
            local_day TEXT NOT NULL,
            direction TEXT NOT NULL,          -- 'kirdi' | 'chiqdi'
            camera TEXT NOT NULL DEFAULT 'entrance'
        );

        CREATE INDEX IF NOT EXISTS idx_face_emp ON face_embeddings(employee_id);
        CREATE INDEX IF NOT EXISTS idx_crossings_day ON crossings(local_day, ts);
        CREATE INDEX IF NOT EXISTS idx_outbox_pending ON telegram_outbox(sent_at);
        CREATE INDEX IF NOT EXISTS idx_attempts_tg ON pairing_attempts(telegram_id, ts);
        CREATE INDEX IF NOT EXISTS idx_presence_emp_ts ON presence_log(employee_id, ts);
        CREATE INDEX IF NOT EXISTS idx_samples_emp_ts ON presence_samples(employee_id, ts);
        CREATE INDEX IF NOT EXISTS idx_att_emp_day ON attendance_events(employee_id, local_day);
        CREATE INDEX IF NOT EXISTS idx_att_ts ON attendance_events(ts);
    """)
    con.commit()
    _migrate_employees(con)
    con.close()


def create_employee(con, first_name: str, last_name: str = "", position: str = "") -> int:
    cur = con.execute(
        "INSERT INTO employees (first_name, last_name, position) VALUES (?, ?, ?)",
        (first_name.strip(), last_name.strip(), position.strip()),
    )
    con.commit()
    return cur.lastrowid


def find_employee_by_name(con, first_name: str, last_name: str = ""):
    return con.execute(
        "SELECT id FROM employees WHERE first_name = ? AND last_name = ?",
        (first_name.strip(), last_name.strip()),
    ).fetchone()


if __name__ == "__main__":
    init_db()
    print(f"DB ready at {DB_PATH}")
