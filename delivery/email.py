import os
import resend
from datetime import date
from core.models import Job


def build_digest(jobs: list[Job]) -> tuple[str, str]:
    """Build email subject and HTML body from scored jobs."""
    today = date.today().strftime("%b %d, %Y")

    high = [j for j in jobs if j.score == "HIGH"]
    medium = [j for j in jobs if j.score == "MEDIUM"]
    low = [j for j in jobs if j.score == "LOW"]

    subject = f"Amane's Jobs — {today} — {len(high)} High, {len(medium)} Medium"

    parts = []
    parts.append(f"<h2>Job Digest — {today}</h2>")
    parts.append(f"<p>{len(jobs)} new jobs found. {len(high)} high fit, {len(medium)} medium, {len(low)} low.</p>")
    parts.append("<hr>")

    if high:
        parts.append("<h3>⭐ HIGH FIT — Customize your application</h3>")
        for i, job in enumerate(high, 1):
            parts.append(_job_card(i, job, include_cover_letter=True))

    if medium:
        parts.append("<h3>📋 MEDIUM FIT — Quick apply with standard letter</h3>")
        for i, job in enumerate(medium, 1):
            parts.append(_job_card(len(high) + i, job, include_cover_letter=False))

    if low:
        parts.append(f"<p><em>Skipped {len(low)} low-fit jobs (mostly require German or wrong field)</em></p>")

    parts.append("<hr>")
    parts.append("<p style='color: #888; font-size: 12px;'>Automated by Amane's Job Discovery Pipeline</p>")

    body = "\n".join(parts)
    return subject, body


def _job_card(num: int, job: Job, include_cover_letter: bool) -> str:
    card = f"""
    <div style="margin-bottom: 20px; padding: 15px; border: 1px solid #ddd; border-radius: 8px;">
        <strong>{num}. {job.title}</strong><br>
        🏢 {job.company} · 📍 {job.location}<br>
        <em>{job.score_reason}</em><br>
        <a href="{job.url}">→ Apply here</a>
    """
    if include_cover_letter and job.cover_letter:
        card += f"""
        <details style="margin-top: 10px;">
            <summary>📝 Draft Cover Letter</summary>
            <pre style="white-space: pre-wrap; font-family: sans-serif; background: #f9f9f9; padding: 10px; border-radius: 4px;">{job.cover_letter}</pre>
        </details>
        """
    card += "</div>"
    return card


def send_digest(jobs: list[Job], recipient_email: str):
    """Send the digest email via Resend API."""
    if not jobs:
        print("[email] No jobs to send.")
        return

    api_key = os.environ.get("RESEND_API_KEY")
    if not api_key:
        print("[email] RESEND_API_KEY not set — printing digest to stdout instead.")
        subject, body = build_digest(jobs)
        print(f"\nSubject: {subject}\n")
        for job in jobs:
            print(f"  [{job.score}] {job.title} at {job.company} ({job.location})")
            print(f"          {job.score_reason}")
            print(f"          {job.url}\n")
        return

    resend.api_key = api_key
    subject, body = build_digest(jobs)

    try:
        result = resend.Emails.send({
            "from": "Amane Jobs <onboarding@resend.dev>",
            "to": [recipient_email],
            "subject": subject,
            "html": body,
        })
        print(f"[email] Sent digest to {recipient_email} — id: {result.get('id')}")
    except Exception as e:
        print(f"[email] Failed to send: {e}")
