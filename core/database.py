import sqlite3
from pathlib import Path
from core.models import Job

DB_PATH = Path(__file__).parent.parent / "data" / "jobs.db"


def get_connection() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(DB_PATH))
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
            score_reason TEXT,
            cover_letter TEXT,
            found_date TEXT,
            status TEXT DEFAULT 'new'
        )
    """)
    conn.commit()
    return conn


def job_exists(conn: sqlite3.Connection, job: Job) -> bool:
    row = conn.execute("SELECT 1 FROM jobs WHERE id = ? OR url = ?", (job.id, job.url)).fetchone()
    return row is not None


def save_job(conn: sqlite3.Connection, job: Job):
    conn.execute(
        """INSERT OR IGNORE INTO jobs
           (id, source, title, company, location, description, url, score, score_reason, cover_letter, found_date, status)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (job.id, job.source, job.title, job.company, job.location, job.description,
         job.url, job.score, job.score_reason, job.cover_letter, job.found_date, job.status),
    )
    conn.commit()


def update_score(conn: sqlite3.Connection, job_id: str, score: str, reason: str):
    conn.execute("UPDATE jobs SET score = ?, score_reason = ? WHERE id = ?", (score, reason, job_id))
    conn.commit()


def update_cover_letter(conn: sqlite3.Connection, job_id: str, letter: str):
    conn.execute("UPDATE jobs SET cover_letter = ? WHERE id = ?", (letter, job_id))
    conn.commit()


def get_todays_jobs(conn: sqlite3.Connection, found_date: str) -> list[Job]:
    rows = conn.execute(
        "SELECT source, title, company, location, description, url, score, score_reason, cover_letter, found_date, status FROM jobs WHERE found_date = ?",
        (found_date,),
    ).fetchall()
    return [
        Job(
            source=r[0], title=r[1], company=r[2], location=r[3], description=r[4],
            url=r[5], score=r[6], score_reason=r[7], cover_letter=r[8],
            found_date=r[9], status=r[10],
        )
        for r in rows
    ]
