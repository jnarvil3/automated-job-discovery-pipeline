"""
Application dispatcher — orchestrates all apply methods.

Strategy priority:
1. ATS API (Greenhouse, Lever, Workable) — most reliable, no bot detection
2. Browser (Playwright) — for Personio, SmartRecruiters, unknown portals
3. Email (Resend) — fallback when apply email is found
4. Manual — last resort, user gets a direct link
"""

import json
import os
import sys
import sqlite3
from pathlib import Path

from core.models import Job
from core.rate_limiter import remaining_applications_today
from core.question_answerer import answer_questions
from delivery.cover_letter import generate_cover_letter, generate_cover_letter_pdf, generate_cover_letter_docx
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
            print(f"    Trying {applicant.platform_name} API...")

            # Fetch custom questions
            questions = applicant.fetch_questions(job)
            question_answers = {}
            if questions:
                print(f"    Answering {len(questions)} screening questions...")
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
        print(f"    [DRY RUN] Would email {job.apply_email}")
        return ApplicationResult(
            success=True, method="email",
            message=f"DRY RUN — would email {job.apply_email}",
            response_data={"dry_run": True},
        )

    resend.api_key = api_key
    sender = candidate.get("email", "onboarding@resend.dev")
    full_name = f"{candidate.get('first_name', '')} {candidate.get('last_name', '')}".strip()

    html_body = f"""
    <p>Dear Hiring Team at {job.company},</p>
    <p>{cover_letter.replace(chr(10), '<br>')}</p>
    <p>Best regards,<br>{full_name}</p>
    <p style="color: #888; font-size: 11px;">Please find my CV attached.</p>
    """

    email_params = {
        "from": f"{full_name} <onboarding@resend.dev>",
        "to": [job.apply_email],
        "subject": f"Application: {job.title} — {full_name}",
        "html": html_body,
    }

    resume = Path(resume_path) if resume_path else None
    if resume and resume.exists():
        with open(resume, "rb") as f:
            email_params["attachments"] = [{
                "filename": f"{full_name.replace(' ', '_')}_CV.pdf",
                "content": list(f.read()),
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
        print("  [dispatcher] Auto-apply disabled in config")
        return jobs

    # Which tiers to auto-apply
    apply_tiers = auto_apply_config.get("tiers", ["MEDIUM"])
    eligible_jobs = [j for j in jobs if j.score in apply_tiers]

    if not eligible_jobs:
        print("  [dispatcher] No eligible jobs for auto-apply")
        return jobs

    # Check rate limit
    max_per_day = auto_apply_config.get("max_per_day", 5)
    remaining = remaining_applications_today(conn, max_per_day)
    if remaining <= 0:
        print(f"  [dispatcher] Daily limit reached ({max_per_day}/day) — skipping auto-apply")
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

    for job in eligible_jobs:
        if applied >= remaining:
            print(f"  [dispatcher] Rate limit reached — {len(eligible_jobs) - applied - failed - skipped} jobs queued for next run")
            job.status = "queued"
            continue

        # Skip if already applied or exceeded retries
        if job.status in ("auto_applied", "applied"):
            continue
        if job.apply_attempts >= max_retries:
            job.status = "apply_failed"
            job.apply_error = f"Exceeded {max_retries} retries"
            failed += 1
            continue

        print(f"\n  [{applied+1}/{min(len(eligible_jobs), remaining)}] {job.title} at {job.company}")

        # Generate cover letter if not already done
        if not job.cover_letter:
            print(f"    Generating cover letter...")
            job.cover_letter = generate_cover_letter(job)

        # Generate PDF and DOCX versions for ATS uploads
        cover_letter_pdf = ""
        cover_letter_docx = ""
        if job.cover_letter:
            cover_letter_pdf = generate_cover_letter_pdf(job, job.cover_letter)
            cover_letter_docx = generate_cover_letter_docx(job, job.cover_letter)
            if cover_letter_pdf:
                print(f"    Generated cover letter PDF: {Path(cover_letter_pdf).name}")

        job.apply_attempts += 1
        result = None

        # Try each method in priority order
        # 1. ATS API
        if methods.get("api", True) and job.ats_platform in ("greenhouse", "lever", "workable"):
            result = _try_api_apply(job, candidate, job.cover_letter, resume_path,
                                    cover_letter_pdf=cover_letter_pdf)

        # 2a. Personio-specific browser handler
        if (result is None or not result.success) and methods.get("browser", True) and job.ats_platform == "personio":
            personio_result = _try_personio_apply(job, candidate, job.cover_letter, resume_path, dry_run,
                                                   cover_letter_pdf=cover_letter_pdf)
            if personio_result:
                result = personio_result

        # 2b. Generic browser (if API and Personio didn't work)
        if (result is None or not result.success) and methods.get("browser", True) and job.ats_platform:
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
            print(f"    -> {result.message}")

            # Log to applications table
            from core.database import log_application
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
            print(f"    -> FAILED: {result.message}")

            from core.database import log_application
            log_application(conn, job.id, result.method, "failed",
                          error_message=result.message,
                          response_data=json.dumps(result.response_data))
        else:
            # No method available — manual apply
            job.status = "quick_apply"
            skipped += 1
            print(f"    -> No auto-apply method available — marked for manual apply")

    print(f"\n  [dispatcher] Applied: {applied} | Failed: {failed} | Manual: {skipped}")
    return jobs
