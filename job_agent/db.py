import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

DB_PATH = Path(__file__).parent.parent / "data" / "job_agent.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (
    id TEXT PRIMARY KEY,
    title TEXT,
    company TEXT,
    location TEXT,
    salary TEXT,
    portal TEXT,
    jd_url TEXT,
    jd_text TEXT,
    relevance_score INTEGER,
    relevance_reason TEXT,
    scraped_at TEXT,
    status TEXT DEFAULT 'new'
);

CREATE TABLE IF NOT EXISTS applications (
    id TEXT PRIMARY KEY,
    job_id TEXT,
    company TEXT,
    role TEXT,
    portal TEXT,
    applied_date TEXT,
    status TEXT DEFAULT 'applied',
    resume_file TEXT,
    jd_url TEXT,
    last_email_update TEXT,
    notes TEXT,
    created_at TEXT,
    apply_method TEXT DEFAULT 'manual'
);

CREATE TABLE IF NOT EXISTS scrape_logs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_at TEXT,
    portal TEXT,
    query TEXT,
    location TEXT,
    jobs_found INTEGER DEFAULT 0,
    jobs_stored INTEGER DEFAULT 0,
    error TEXT
);

CREATE TABLE IF NOT EXISTS tailor_logs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_at TEXT,
    job_id TEXT,
    company TEXT,
    pdf_path TEXT,
    ats_passed INTEGER DEFAULT 0,
    dry_run INTEGER DEFAULT 0,
    error TEXT
);
"""

# apply_method lives in both SCHEMA (fresh installs) and _MIGRATION_V2 (existing DBs via ALTER)
_MIGRATION_V2 = "ALTER TABLE applications ADD COLUMN apply_method TEXT DEFAULT 'manual'"


@contextmanager
def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_db():
    with get_db() as conn:
        conn.executescript(SCHEMA)
        existing_cols = {r[1] for r in conn.execute("PRAGMA table_info(applications)").fetchall()}
        if "apply_method" not in existing_cols:
            conn.execute(_MIGRATION_V2)


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def upsert_job(conn, job: dict):
    conn.execute(
        """INSERT OR REPLACE INTO jobs
           (id, title, company, location, salary, portal, jd_url, jd_text,
            relevance_score, relevance_reason, scraped_at, status)
           VALUES (:id,:title,:company,:location,:salary,:portal,:jd_url,:jd_text,
                   :relevance_score,:relevance_reason,:scraped_at,:status)""",
        job,
    )


def get_job(conn, job_id: str):
    return conn.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()


def get_jobs(conn, status=None, min_score=0, portal=None):
    clauses, params = ["relevance_score >= ?"], [min_score]
    if status:
        clauses.append("status = ?")
        params.append(status)
    if portal:
        clauses.append("portal = ?")
        params.append(portal)
    where = " AND ".join(clauses)
    return conn.execute(
        f"SELECT * FROM jobs WHERE {where} ORDER BY relevance_score DESC, scraped_at DESC",
        params,
    ).fetchall()


def insert_application(conn, app: dict):
    conn.execute(
        """INSERT OR REPLACE INTO applications
           (id, job_id, company, role, portal, applied_date, status,
            resume_file, jd_url, last_email_update, notes, created_at, apply_method)
           VALUES (:id,:job_id,:company,:role,:portal,:applied_date,:status,
                   :resume_file,:jd_url,:last_email_update,:notes,:created_at,
                   :apply_method)""",
        app,
    )


def get_applications(conn, status=None):
    if status:
        return conn.execute(
            "SELECT * FROM applications WHERE status=? ORDER BY applied_date DESC", (status,)
        ).fetchall()
    return conn.execute(
        "SELECT * FROM applications ORDER BY applied_date DESC"
    ).fetchall()


def update_application(conn, app_id: str, **fields):
    if not fields:
        return
    set_clause = ", ".join(f"{k}=?" for k in fields)
    conn.execute(
        f"UPDATE applications SET {set_clause} WHERE id=?",
        [*fields.values(), app_id],
    )


def get_application_by_job(conn, job_id: str):
    return conn.execute(
        "SELECT * FROM applications WHERE job_id=? ORDER BY created_at DESC LIMIT 1",
        (job_id,),
    ).fetchone()


def log_scrape(conn, portal: str, query: str, location: str, jobs_found=0, jobs_stored=0, error=None):
    conn.execute(
        """INSERT INTO scrape_logs (run_at, portal, query, location, jobs_found, jobs_stored, error)
           VALUES (?,?,?,?,?,?,?)""",
        (now_iso(), portal, query, location, jobs_found, jobs_stored, error),
    )


def log_tailor(conn, job_id: str, company: str, pdf_path=None, ats_passed=False, dry_run=False, error=None):
    conn.execute(
        """INSERT INTO tailor_logs (run_at, job_id, company, pdf_path, ats_passed, dry_run, error)
           VALUES (?,?,?,?,?,?,?)""",
        (now_iso(), job_id, company, pdf_path, int(ats_passed), int(dry_run), error),
    )


if __name__ == "__main__":
    init_db()
    print(f"Database initialised at {DB_PATH}")
