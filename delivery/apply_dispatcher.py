"""
Application dispatcher — orchestrates all apply methods.

Strategy priority:
1. ATS API (Greenhouse, Lever, Workable) — most reliable, no bot detection
2. Browser (Playwright) — for Personio, SmartRecruiters, unknown portals
3. Email (Resend) — fallback when apply email is found
4. Manual — last resort, user gets a direct link
"""

import base64
import html
import json
import logging
import os
import sys
import sqlite3
from pathlib import Path

log = logging.getLogger(__name__)

from core.models import Job
from core.rate_limiter import remaining_applications_today
from core.database import log_application
from core.question_answerer import answer_questions
from delivery.cover_letter import generate_cover_letter, generate_cover_letter_pdf
from delivery.ats.base import ApplicationResult
from delivery.ats.greenhouse import GreenhouseApplicant
from delivery.ats.lever import LeverApplicant
from delivery.ats.workable import WorkableApplicant


# ATS applicants in priority order
ATS_APPLICANTS = [
    GreenhouseApplicant(),
    LeverApplicant(),
    WorkableApplicant(),
]


def _build_candidate(profile: dict) -> dict:
    """Build candidate data dict from profile config + env vars."""
    candidate = profile.get("candidate", {})
    return {
        "first_name": profile.get("first_name", ""),
        "last_name": profile.get("last_name", ""),
        "email": profile.get("email") or os.environ.get("AMANE_EMAIL", ""),
        "phone": profile.get("phone") or os.environ.get("AMANE_PHONE", ""),
        "linkedin_url": candidate.get("linkedin_url") or os.environ.get("AMANE_LINKEDIN", ""),
        "headline": candidate.get("headline", ""),
        "current_location": candidate.get("current_location", "Germany"),
        "work_authorization": candidate.get("work_authorization", ""),
        "screening_answers": candidate.get("screening_answers", {}),
        "how_did_you_hear": candidate.get("how_did_you_hear", "Job board"),
    }


def _try_api_apply(job: Job, candidate: dict, cover_letter: str,
                   resume_path: str, cover_letter_pdf: str = "") -> ApplicationResult | None:
    """Try to apply via ATS API. Returns None if no API available."""
    for applicant in ATS_APPLICANTS:
        if applicant.can_apply(job):
            log.info("Trying %s API...", applicant.platform_name)

            # Fetch custom questions
            questions = applicant.fetch_questions(job)
            question_answers = {}
            if questions:
                log.info("Answering %d screening questions...", len(questions))
                question_answers = answer_questions(questions, candidate, job)

            # Submit — pass PDF path if available for file upload fields
            result = applicant.submit(job, candidate, cover_letter, resume_path,
                                      question_answers, cover_letter_pdf=cover_letter_pdf)
            return result

    return None


def _try_email_apply(job: Job, candidate: dict, cover_letter: str,
                     resume_path: str, dry_run: bool) -> ApplicationResult | None:
    """Try to apply via email (existing Resend-based approach)."""
    if not job.apply_email:
        return None

    import resend

    api_key = os.environ.get("RESEND_API_KEY")
    if not api_key:
        return ApplicationResult(
            success=False, method="email",
            message="RESEND_API_KEY not set", response_data={},
        )

    if dry_run:
        log.info("[DRY RUN] Would email %s", job.apply_email)
        return ApplicationResult(
            success=True, method="email",
            message=f"DRY RUN — would email {job.apply_email}",
            response_data={"dry_run": True},
        )

    sender_email = os.environ.get("SENDER_EMAIL", "")
    if not sender_email:
        return ApplicationResult(
            success=False, method="email",
            message="SENDER_EMAIL not set — refusing to send from test domain",
            response_data={},
        )

    resend.api_key = api_key
    full_name = f"{candidate.get('first_name', '')} {candidate.get('last_name', '')}".strip()

    html_body = f"""
    <p>Dear Hiring Team at {html.escape(job.company)},</p>
    <p>{html.escape(cover_letter).replace(chr(10), '<br>')}</p>
    <p>Best regards,<br>{html.escape(full_name)}</p>
    <p style="color: #888; font-size: 11px;">Please find my CV attached.</p>
    """

    email_params = {
        "from": f"{full_name} <{sender_email}>",
        "to": [job.apply_email],
        "subject": f"Application: {job.title} — {full_name}",
        "html": html_body,
    }

    resume = Path(resume_path) if resume_path else None
    if resume and resume.exists():
        with open(resume, "rb") as f:
            email_params["attachments"] = [{
                "filename": f"{full_name.replace(' ', '_')}_CV.pdf",
                "content": base64.b64encode(f.read()).decode(),
            }]

    try:
        result = resend.Emails.send(email_params)
        return ApplicationResult(
            success=True, method="email",
            message=f"Application emailed to {job.apply_email}",
            response_data={"email_id": result.get("id", "")},
        )
    except Exception as e:
        return ApplicationResult(
            success=False, method="email",
            message=f"Email failed: {e}",
            response_data={"error": str(e)},
        )


def _try_personio_apply(job: Job, candidate: dict, cover_letter: str,
                        resume_path: str, dry_run: bool,
                        cover_letter_pdf: str = "") -> ApplicationResult | None:
    """Try to apply via the Personio-specific browser handler."""
    try:
        from delivery.browser.personio import personio_apply
        return personio_apply(job, candidate, cover_letter, resume_path,
                              dry_run=dry_run, cover_letter_pdf=cover_letter_pdf)
    except ImportError:
        return None
    except Exception as e:
        return ApplicationResult(
            success=False, method="browser_personio",
            message=f"Personio apply failed: {e}",
            response_data={"error": str(e)},
        )


def _try_browser_apply(job: Job, candidate: dict, cover_letter: str,
                       resume_path: str, dry_run: bool,
                       cover_letter_pdf: str = "") -> ApplicationResult | None:
    """Try to apply via browser automation (Playwright). Returns None if not available."""
    try:
        from delivery.browser.engine import browser_apply
        return browser_apply(job, candidate, cover_letter, resume_path,
                             dry_run=dry_run, cover_letter_pdf=cover_letter_pdf)
    except ImportError:
        # Playwright not installed — skip browser apply
        return None
    except Exception as e:
        return ApplicationResult(
            success=False, method="browser",
            message=f"Browser apply failed: {e}",
            response_data={"error": str(e)},
        )


def apply_to_jobs(jobs: list[Job], profile: dict, conn: sqlite3.Connection,
                  dry_run: bool = True) -> list[Job]:
    """
    Apply to eligible jobs using the best available method.

    Strategy: API > browser > email > manual
    Only applies to MEDIUM-tier jobs (configurable in profile).
    Respects daily rate limit.
    """
    auto_apply_config = profile.get("auto_apply", {})
    if not auto_apply_config.get("enabled", True):
        log.info("Auto-apply disabled in config")
        return jobs

    # Which tiers to auto-apply
    apply_tiers = auto_apply_config.get("tiers", ["MEDIUM"])
    eligible_jobs = [j for j in jobs if j.score in apply_tiers]

    if not eligible_jobs:
        log.info("No eligible jobs for auto-apply")
        return jobs

    # Check rate limit
    max_per_day = auto_apply_config.get("max_per_day", 5)
    remaining = remaining_applications_today(conn, max_per_day)
    if remaining <= 0:
        log.info("Daily limit reached (%d/day) — skipping auto-apply", max_per_day)
        for job in eligible_jobs:
            job.status = "queued"
        return jobs

    # Which methods are enabled
    methods = auto_apply_config.get("methods", {"api": True, "browser": True, "email": True})
    candidate = _build_candidate(profile)
    resume_path = profile.get("resume_path", "")
    max_retries = auto_apply_config.get("max_retries", 3)

    applied = 0
    failed = 0
    skipped = 0

    # Per-company dedup: check which companies already have applications this week
    applied_companies: set[str] = set()
    try:
        rows = conn.execute(
            """SELECT DISTINCT LOWER(j.company) FROM applications a
               JOIN jobs j ON a.job_id = j.id
               WHERE a.status = 'success'
               AND a.submitted_at >= date('now', '-7 days')""",
        ).fetchall()
        applied_companies = {r[0] for r in rows}
    except Exception:
        pass  # Table may not have data yet

    for job in eligible_jobs:
        if applied >= remaining:
            log.info("Rate limit reached — %d jobs queued for next run", len(eligible_jobs) - applied - failed - skipped)
            job.status = "queued"
            continue

        # Skip if already applied or exceeded retries
        if job.status in ("auto_applied", "applied"):
            continue
        # Per-company dedup: skip if we already applied to this company recently
        company_key = job.company.strip().lower()
        if company_key in applied_companies:
            job.status = "apply_skipped_company_dup"
            skipped += 1
            log.info("[%d/%d] %s at %s — SKIPPED (already applied this week)", applied+1, min(len(eligible_jobs), remaining), job.title, job.company)
            continue
        if job.apply_attempts >= max_retries:
            job.status = "apply_failed"
            job.apply_error = f"Exceeded {max_retries} retries"
            failed += 1
            continue

        log.info("[%d/%d] %s at %s", applied+1, min(len(eligible_jobs), remaining), job.title, job.company)

        # Generate cover letter if not already done
        if not job.cover_letter:
            log.info("Generating cover letter...")
            job.cover_letter = generate_cover_letter(job)
            if not job.cover_letter:
                log.warning("Cover letter generation failed — skipping auto-apply for %s", job.title)
                job.status = "quick_apply"
                skipped += 1
                continue

        job.apply_attempts += 1
        result = None
        cover_letter_pdf = ""

        # Try each method in priority order
        # 1. ATS API
        if methods.get("api", True) and job.ats_platform in ("greenhouse", "lever", "workable"):
            if job.cover_letter and not cover_letter_pdf:
                cover_letter_pdf = generate_cover_letter_pdf(job, job.cover_letter)
                if cover_letter_pdf:
                    log.info("Generated cover letter PDF: %s", Path(cover_letter_pdf).name)
            result = _try_api_apply(job, candidate, job.cover_letter, resume_path,
                                    cover_letter_pdf=cover_letter_pdf)

        # 2a. Personio-specific browser handler
        if (result is None or not result.success) and methods.get("browser", True) and job.ats_platform == "personio":
            if job.cover_letter and not cover_letter_pdf:
                cover_letter_pdf = generate_cover_letter_pdf(job, job.cover_letter)
                if cover_letter_pdf:
                    log.info("Generated cover letter PDF: %s", Path(cover_letter_pdf).name)
            personio_result = _try_personio_apply(job, candidate, job.cover_letter, resume_path, dry_run,
                                                   cover_letter_pdf=cover_letter_pdf)
            if personio_result:
                result = personio_result

        # 2b. Generic browser (if API and Personio didn't work)
        if (result is None or not result.success) and methods.get("browser", True) and job.ats_platform:
            if job.cover_letter and not cover_letter_pdf:
                cover_letter_pdf = generate_cover_letter_pdf(job, job.cover_letter)
                if cover_letter_pdf:
                    log.info("Generated cover letter PDF: %s", Path(cover_letter_pdf).name)
            browser_result = _try_browser_apply(job, candidate, job.cover_letter, resume_path, dry_run,
                                                cover_letter_pdf=cover_letter_pdf)
            if browser_result:
                result = browser_result

        # 3. Email fallback
        if (result is None or not result.success) and methods.get("email", True) and job.apply_email:
            email_result = _try_email_apply(job, candidate, job.cover_letter, resume_path, dry_run)
            if email_result:
                result = email_result

        # Process result
        if result and result.success:
            job.status = "auto_applied"
            job.apply_method = result.method
            job.apply_error = ""
            applied += 1
            applied_companies.add(company_key)
            log.info("-> %s", result.message)

            # Log to applications table
            log_application(conn, job.id, result.method, "success",
                          response_data=json.dumps(result.response_data))

        elif result and not result.success:
            job.apply_error = result.message
            job.apply_method = result.method
            if "captcha" in result.message.lower():
                job.status = "apply_skipped_captcha"
                skipped += 1
            else:
                job.status = "apply_failed" if job.apply_attempts >= max_retries else "new"
                failed += 1
            log.error("-> FAILED: %s", result.message)

            log_application(conn, job.id, result.method, "failed",
                          error_message=result.message,
                          response_data=json.dumps(result.response_data))
        else:
            # No method available — manual apply
            job.status = "quick_apply"
            skipped += 1
            log.info("-> No auto-apply method available — marked for manual apply")

    log.info("Applied: %d | Failed: %d | Manual: %d", applied, failed, skipped)
    return jobs
