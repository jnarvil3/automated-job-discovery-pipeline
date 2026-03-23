"""
Auto-apply for MEDIUM-tier jobs.

Sends a standard application email (with resume) to jobs where an
application email was extracted. For jobs without a known email,
marks them as "quick_apply" so the digest can show a direct link.
"""

import json
import os
from pathlib import Path

import resend
from openai import OpenAI
from core.models import Job


LETTER_PROMPT = """You write short, professional application emails for a specific candidate. Return ONLY the email body text — no subject line, no greeting, no signature (those are added separately).

CANDIDATE:
- Amane Dias, international master's student in Germany (Brazilian)
- Finishing thesis this semester
- Looking for Working Student / Internship roles
- Fields: Finance, FP&A, Controlling, Sustainability, Renewable Energy, Back Office, Marketing
- Languages: English (fluent), Portuguese (native), German (A1 — basic only)

RULES:
- Keep it under 150 words
- Be warm but professional — not generic
- Reference the specific company and role
- Highlight her international perspective and relevant academic background
- Do NOT mention German skills — only mention English and Portuguese
- End with enthusiasm about contributing to the team
"""


def generate_cover_letter(job: Job, client: OpenAI) -> str:
    """Generate a short, tailored application letter for the job."""
    try:
        response = client.chat.completions.create(
            model="gpt-4o-mini",
            max_tokens=300,
            messages=[
                {"role": "system", "content": LETTER_PROMPT},
                {"role": "user", "content": f"Write an application email body for:\nRole: {job.title}\nCompany: {job.company}\nLocation: {job.location}\nDescription: {job.description[:800]}"},
            ],
        )
        return response.choices[0].message.content.strip()
    except Exception as e:
        print(f"  [auto-apply] Failed to generate letter for {job.title}: {e}")
        return ""


def send_application(job: Job, letter: str, resume_path: str, sender_email: str) -> bool:
    """Send application email via Resend with resume attached."""
    api_key = os.environ.get("RESEND_API_KEY")
    if not api_key:
        print(f"  [auto-apply] RESEND_API_KEY not set — skipping {job.title}")
        return False

    if not job.apply_email:
        return False

    resend.api_key = api_key

    # Build email
    subject = f"Application: {job.title} — Amane Dias"

    html_body = f"""
    <p>Dear Hiring Team at {job.company},</p>
    <p>{letter.replace(chr(10), '<br>')}</p>
    <p>Best regards,<br>Amane Dias</p>
    <p style="color: #888; font-size: 11px;">Please find my CV attached.</p>
    """

    email_params = {
        "from": sender_email or "Amane Dias <onboarding@resend.dev>",
        "to": [job.apply_email],
        "subject": subject,
        "html": html_body,
    }

    # Attach resume if it exists
    resume = Path(resume_path) if resume_path else None
    if resume and resume.exists():
        with open(resume, "rb") as f:
            email_params["attachments"] = [{
                "filename": "Amane_Dias_CV.pdf",
                "content": list(f.read()),
            }]

    try:
        result = resend.Emails.send(email_params)
        print(f"  [auto-apply] Sent to {job.apply_email} for '{job.title}' — id: {result.get('id')}")
        return True
    except Exception as e:
        print(f"  [auto-apply] Failed to send for '{job.title}': {e}")
        return False


def auto_apply_jobs(jobs: list[Job], profile: dict) -> list[Job]:
    """
    Auto-apply to MEDIUM-tier jobs.

    - If apply_email is available: send application + mark as auto_applied
    - If no email: mark as quick_apply (user gets direct link in digest)

    Returns the same list with updated statuses.
    """
    medium_jobs = [j for j in jobs if j.score == "MEDIUM"]
    if not medium_jobs:
        return jobs

    openai_key = os.environ.get("OPENAI_API_KEY")
    if not openai_key:
        print("[auto-apply] OPENAI_API_KEY not set — skipping auto-apply")
        return jobs

    client = OpenAI(api_key=openai_key)
    resume_path = profile.get("resume_path", "")
    sender_email = profile.get("sender_email", "")
    applied_count = 0
    quick_apply_count = 0

    for job in medium_jobs:
        # Generate a standard cover letter
        letter = generate_cover_letter(job, client)
        if letter:
            job.cover_letter = letter

        if job.apply_email:
            # Send the application
            if "--send" in __import__("sys").argv:
                success = send_application(job, letter, resume_path, sender_email)
                if success:
                    job.status = "auto_applied"
                    applied_count += 1
                else:
                    job.status = "quick_apply"
                    quick_apply_count += 1
            else:
                print(f"  [auto-apply] DRY RUN — would email {job.apply_email} for '{job.title}'")
                job.status = "auto_apply_pending"
                applied_count += 1
        else:
            job.status = "quick_apply"
            quick_apply_count += 1

    print(f"  [auto-apply] Applied: {applied_count} | Quick-apply (manual link): {quick_apply_count}")
    return jobs
