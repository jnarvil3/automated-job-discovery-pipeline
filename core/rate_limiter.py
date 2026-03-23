"""
Daily application rate limiter.
Prevents over-applying which could trigger bans or look spammy.
"""

import sqlite3
from datetime import date


MAX_DAILY_APPLICATIONS = 5


def remaining_applications_today(conn: sqlite3.Connection, max_per_day: int = MAX_DAILY_APPLICATIONS) -> int:
    """Returns number of remaining applications allowed today."""
    today = date.today().isoformat()

    # Count successful applications from the applications table
    try:
        row = conn.execute(
            "SELECT COUNT(*) FROM applications WHERE DATE(submitted_at) = ? AND status = 'success'",
            (today,),
        ).fetchone()
        today_count = row[0] if row else 0
    except Exception:
        # Table might not exist yet — no applications sent
        today_count = 0

    # Also count jobs with auto_applied status from today as a fallback
    try:
        row = conn.execute(
            "SELECT COUNT(*) FROM jobs WHERE found_date = ? AND status = 'auto_applied'",
            (today,),
        ).fetchone()
        today_count = max(today_count, row[0] if row else 0)
    except Exception:
        pass

    return max(0, max_per_day - today_count)
