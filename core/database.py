import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from core.models import Job

DB_PATH = Path(__file__).parent.parent / "data" / "jobs.db"


def get_connection() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(DB_PATH))
    conn.execute("PRAGMA journal_mode=WAL")  # Better concurrency

    conn.execute("""
        CREATE TABLE IF NOT EXISTS jobs (
            id TEXT PRIMARY KEY,
            source TEXT,
            title TEXT,
            company TEXT,
            location TEXT,
            description TEXT,
            url TEXT UNIQUE,
            score TEXT,
            fit_score INTEGER DEFAULT 0,
            score_reason TEXT,
            cover_letter TEXT,
            found_date TEXT,
            status TEXT DEFAULT 'new',
            apply_email TEXT DEFAULT '',
            ats_platform TEXT DEFAULT '',
            ats_job_id TEXT DEFAULT '',
            ats_board_token TEXT DEFAULT '',
            apply_method TEXT DEFAULT '',
            apply_attempts INTEGER DEFAULT 0,
            apply_error TEXT DEFAULT '',
            posted_date TEXT DEFAULT ''
        )
    """)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS applications (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            job_id TEXT REFERENCES jobs(id),
            method TEXT,
            status TEXT,
            submitted_at TEXT,
            error_message TEXT,
            response_data TEXT
        )
    """)

    conn.execute("CREATE INDEX IF NOT EXISTS idx_jobs_found_date ON jobs(found_date)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_jobs_status ON jobs(status)")

    # Migrate existing tables that lack new columns
    _migrate_columns = [
        ("fit_score", "INTEGER DEFAULT 0"),
        ("apply_email", "TEXT DEFAULT ''"),
        ("ats_platform", "TEXT DEFAULT ''"),
        ("ats_job_id", "TEXT DEFAULT ''"),
        ("ats_board_token", "TEXT DEFAULT ''"),
        ("apply_method", "TEXT DEFAULT ''"),
        ("apply_attempts", "INTEGER DEFAULT 0"),
        ("apply_error", "TEXT DEFAULT ''"),
        ("posted_date", "TEXT DEFAULT ''"),
    ]
    for col_name, col_type in _migrate_columns:
        try:
            conn.execute(f"ALTER TABLE jobs ADD COLUMN {col_name} {col_type}")
        except Exception:
            pass

    # One-time backfill: set fit_score for jobs scored before fit_score was wired in
    conn.execute("UPDATE jobs SET fit_score = 8 WHERE fit_score = 0 AND score = 'HIGH'")
    conn.execute("UPDATE jobs SET fit_score = 5 WHERE fit_score = 0 AND score = 'MEDIUM'")

    conn.commit()
    return conn


def cleanup_duplicates(conn: sqlite3.Connection) -> int:
    """Remove duplicate jobs by title+company, keeping the earliest entry."""
    cursor = conn.execute("""
        DELETE FROM jobs WHERE rowid NOT IN (
            SELECT MIN(rowid) FROM jobs
            GROUP BY LOWER(TRIM(title)), LOWER(TRIM(company))
        )
    """)
    removed = cursor.rowcount
    conn.commit()
    return removed


def job_exists(conn: sqlite3.Connection, job: Job) -> bool:
    row = conn.execute("SELECT 1 FROM jobs WHERE id = ? OR url = ?", (job.id, job.url)).fetchone()
    return row is not None


def job_exists_by_title_company(conn: sqlite3.Connection, title: str, company: str) -> bool:
    """Check if a job with the same title+company already exists (catches cross-source dupes)."""
    row = conn.execute(
        "SELECT 1 FROM jobs WHERE LOWER(title) = LOWER(?) AND LOWER(company) = LOWER(?)",
        (title.strip(), company.strip()),
    ).fetchone()
    return row is not None


def save_job(conn: sqlite3.Connection, job: Job):
    conn.execute(
        """INSERT INTO jobs
           (id, source, title, company, location, description, url, score, fit_score,
            score_reason, cover_letter, found_date, status, apply_email,
            ats_platform, ats_job_id, ats_board_token, apply_method, apply_attempts, apply_error,
            posted_date)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
           ON CONFLICT(id) DO UPDATE SET
               score = excluded.score,
               fit_score = excluded.fit_score,
               score_reason = excluded.score_reason,
               description = excluded.description,
               cover_letter = CASE WHEN excluded.cover_letter != '' THEN excluded.cover_letter ELSE jobs.cover_letter END,
               status = CASE WHEN excluded.status NOT IN ('new', '') THEN excluded.status ELSE jobs.status END,
               apply_method = CASE WHEN excluded.apply_method != '' THEN excluded.apply_method ELSE jobs.apply_method END,
               apply_attempts = MAX(excluded.apply_attempts, jobs.apply_attempts),
               apply_error = CASE WHEN excluded.apply_error != '' THEN excluded.apply_error ELSE jobs.apply_error END,
               posted_date = CASE WHEN excluded.posted_date != '' THEN excluded.posted_date ELSE jobs.posted_date END""",
        (job.id, job.source, job.title, job.company, job.location, job.description,
         job.url, job.score, job.fit_score, job.score_reason, job.cover_letter,
         job.found_date, job.status, job.apply_email,
         job.ats_platform, job.ats_job_id, job.ats_board_token, job.apply_method,
         job.apply_attempts, job.apply_error, job.posted_date),
    )
    conn.commit()


def log_application(conn: sqlite3.Connection, job_id: str, method: str, status: str,
                    error_message: str = "", response_data: str = ""):
    """Log an application attempt to the applications tracking table."""
    conn.execute(
        """INSERT INTO applications (job_id, method, status, submitted_at, error_message, response_data)
           VALUES (?, ?, ?, ?, ?, ?)""",
        (job_id, method, status, datetime.now(timezone.utc).isoformat(), error_message, response_data),
    )
    conn.commit()


def get_retry_candidates(conn: sqlite3.Connection, max_extra_retries: int = 2) -> list[Job]:
    """Return jobs that failed to apply but are eligible for cross-run retries.

    Criteria:
    - status = 'apply_failed'
    - apply_attempts < max_retries (3) + max_extra_retries
    - found_date within last 14 days (stale postings aren't worth retrying)
    """
    max_attempts = 3 + max_extra_retries
    rows = conn.execute(
        """SELECT source, title, company, location, description, url, score, fit_score,
                  score_reason, cover_letter, found_date, status, apply_email,
                  ats_platform, ats_job_id, ats_board_token, apply_method, apply_attempts, apply_error,
                  posted_date
           FROM jobs
           WHERE status = 'apply_failed'
             AND apply_attempts < ?
             AND found_date >= date('now', '-14 days')""",
        (max_attempts,),
    ).fetchall()
    return [
        Job(
            source=r[0], title=r[1], company=r[2], location=r[3], description=r[4],
            url=r[5], score=r[6], fit_score=r[7], score_reason=r[8], cover_letter=r[9],
            found_date=r[10], status=r[11], apply_email=r[12],
            ats_platform=r[13], ats_job_id=r[14], ats_board_token=r[15],
            apply_method=r[16], apply_attempts=r[17], apply_error=r[18],
            posted_date=r[19] or "",
        )
        for r in rows
    ]


def update_score(conn: sqlite3.Connection, job_id: str, score: str, reason: str):
    conn.execute("UPDATE jobs SET score = ?, score_reason = ? WHERE id = ?", (score, reason, job_id))
    conn.commit()


def update_cover_letter(conn: sqlite3.Connection, job_id: str, letter: str):
    conn.execute("UPDATE jobs SET cover_letter = ? WHERE id = ?", (letter, job_id))
    conn.commit()


