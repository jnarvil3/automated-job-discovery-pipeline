import html
import logging
import os
import resend
from datetime import date
from core.models import Job

log = logging.getLogger(__name__)


def build_digest(jobs: list[Job]) -> tuple[str, str]:
    """Build email subject and HTML body from scored jobs."""
    today = date.today().strftime("%b %d, %Y")

    high = [j for j in jobs if j.score == "HIGH"]
    medium = [j for j in jobs if j.score == "MEDIUM"]
    low = [j for j in jobs if j.score == "LOW"]

    # Split medium by apply result
    api_applied = [j for j in medium if j.status == "auto_applied" and j.apply_method.startswith("api_")]
    browser_applied = [j for j in medium if j.status == "auto_applied" and j.apply_method == "browser"]
    email_applied = [j for j in medium if j.status == "auto_applied" and j.apply_method == "email"]
    auto_applied = api_applied + browser_applied + email_applied
    needs_attention = [j for j in medium if j.status in ("apply_failed", "apply_skipped_captcha")]
    quick_apply = [j for j in medium if j.status not in ("auto_applied", "apply_failed", "apply_skipped_captcha")]

    subject = f"Amane's Jobs — {today} — {len(high)} Top, {len(auto_applied)} Auto-Applied"

    parts = []
    parts.append(f"<h2>Job Digest — {today}</h2>")
    parts.append(f"<p>{len(high)} top matches · {len(auto_applied)} auto-applied · {len(needs_attention)} need attention · {len(quick_apply)} manual</p>")
    parts.append("<hr>")

    # --- TOP matches ---
    if high:
        parts.append(f"<h3>⭐ TOP {len(high)} — Review & customize your application</h3>")
        for i, job in enumerate(high, 1):
            parts.append(_job_card(i, job, include_cover_letter=True))

    # --- Auto-applied via API ---
    if api_applied:
        parts.append("<h3>✅ AUTO-APPLIED (API) — Submitted directly to ATS</h3>")
        parts.append("<p><em>Applications submitted via Greenhouse/Lever/Workable API:</em></p>")
        for i, job in enumerate(api_applied, 1):
            parts.append(_job_card(i, job, include_cover_letter=True, show_auto_applied=True))

    # --- Auto-applied via Browser ---
    if browser_applied:
        parts.append("<h3>✅ AUTO-APPLIED (Browser) — Form filled & submitted</h3>")
        for i, job in enumerate(browser_applied, 1):
            parts.append(_job_card(i, job, include_cover_letter=True, show_auto_applied=True))

    # --- Auto-applied via Email ---
    if email_applied:
        parts.append("<h3>✅ AUTO-APPLIED (Email) — Application emailed</h3>")
        for i, job in enumerate(email_applied, 1):
            parts.append(_job_card(i, job, include_cover_letter=True, show_auto_applied=True))

    # --- Needs manual attention ---
    if needs_attention:
        parts.append("<h3>⚠️ NEEDS ATTENTION — Auto-apply failed</h3>")
        parts.append("<p><em>These jobs couldn't be auto-applied (CAPTCHA, form error, etc.):</em></p>")
        for i, job in enumerate(needs_attention, 1):
            error_note = f' <span style="color: #856404; font-size: 12px;">({job.apply_error[:80]})</span>' if job.apply_error else ""
            parts.append(_job_card(i, job, include_cover_letter=True, extra_html=error_note))

    # --- Quick apply (manual) ---
    if quick_apply:
        parts.append("<h3>🔗 QUICK APPLY — Apply manually</h3>")
        for i, job in enumerate(quick_apply, 1):
            parts.append(_job_card(i, job, include_cover_letter=True))

    if low:
        parts.append(f"<p><em>Skipped {len(low)} low-fit jobs (require German, wrong field, or senior roles)</em></p>")

    parts.append("<hr>")
    parts.append("<p style='color: #888; font-size: 12px;'>Automated by Amane's Job Discovery Pipeline</p>")

    inner = "\n".join(parts)
    body = f"""<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<style>
body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif; max-width: 600px; margin: auto; padding: 16px; color: #333; }}
a {{ color: #1a73e8; }}
</style>
</head>
<body>
{inner}
</body>
</html>"""
    return subject, body


def _job_card(num: int, job: Job, include_cover_letter: bool,
              show_auto_applied: bool = False, extra_html: str = "") -> str:
    status_badge = ""
    if show_auto_applied:
        method_label = {
            "api_greenhouse": "Greenhouse",
            "api_lever": "Lever",
            "api_workable": "Workable",
            "browser": "Browser",
            "email": "Email",
        }.get(job.apply_method, "Auto")
        status_badge = f' <span style="background: #d4edda; color: #155724; padding: 2px 8px; border-radius: 4px; font-size: 12px;">✅ {method_label}</span>'

    card = f"""
    <div style="margin-bottom: 20px; padding: 15px; border: 1px solid #ddd; border-radius: 8px;">
        <strong>{num}. {job.title}</strong>{status_badge}{extra_html}<br>
        🏢 {job.company} · 📍 {job.location}<br>
        <em>{job.score_reason}</em><br>
        <a href="{job.url}">→ View posting</a>
    """
    if include_cover_letter and job.cover_letter:
        label = "📝 Letter Sent" if show_auto_applied else "📝 Draft Cover Letter"
        card += f"""
        <details style="margin-top: 10px;">
            <summary>{label}</summary>
            <pre style="white-space: pre-wrap; font-family: sans-serif; background: #f9f9f9; padding: 10px; border-radius: 4px;">{html.escape(job.cover_letter)}</pre>
        </details>
        """
    card += "</div>"
    return card


def send_digest(jobs: list[Job], recipient_email: str):
    """Send the digest email via Resend API."""
    if not jobs:
        log.info("No jobs to send.")
        return

    api_key = os.environ.get("RESEND_API_KEY")
    if not api_key:
        log.warning("RESEND_API_KEY not set — printing digest to stdout instead.")
        subject, body = build_digest(jobs)
        log.info("Subject: %s", subject)
        for job in jobs:
            log.info("  [%s] %s at %s (%s) — %s — %s", job.score, job.title, job.company, job.location, job.score_reason, job.url)
        return

    sender_email = os.environ.get("SENDER_EMAIL", "")
    if not sender_email:
        log.warning("SENDER_EMAIL not set — refusing to send from test domain. Set SENDER_EMAIL env var.")
        subject, body = build_digest(jobs)
        log.info("Subject: %s", subject)
        for job in jobs:
            log.info("  [%s] %s at %s (%s) — %s — %s", job.score, job.title, job.company, job.location, job.score_reason, job.url)
        return

    resend.api_key = api_key
    subject, body = build_digest(jobs)

    try:
        result = resend.Emails.send({
            "from": f"Amane Jobs <{sender_email}>",
            "to": [recipient_email],
            "subject": subject,
            "html": body,
        })
        log.info("Sent digest to %s — id: %s", recipient_email, result.get('id'))
    except Exception as e:
        log.error("Failed to send: %s", e)
