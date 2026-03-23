"""
Workable API integration.

Uses the public apply endpoint available on Workable-hosted career pages.
"""

import json
import urllib.request
from pathlib import Path
import base64
from core.models import Job
from delivery.ats.base import ATSApplicant, ApplicationResult


class WorkableApplicant(ATSApplicant):
    platform_name = "workable"

    def can_apply(self, job: Job) -> bool:
        return bool(job.ats_platform == "workable" and job.ats_board_token and job.ats_job_id)

    def fetch_questions(self, job: Job) -> list[dict]:
        """Fetch job questions from Workable."""
        url = f"https://apply.workable.com/api/v1/widget/accounts/{job.ats_board_token}/jobs/{job.ats_job_id}/form"
        try:
            req = urllib.request.Request(url, headers={
                "User-Agent": "AmaneJobBot/2.0",
                "Accept": "application/json",
            })
            with urllib.request.urlopen(req, timeout=10) as resp:
                data = json.loads(resp.read().decode())
            return data.get("questions", [])
        except Exception as e:
            print(f"    [workable] Failed to fetch questions: {e}")
            return []

    def submit(self, job: Job, candidate: dict, cover_letter: str,
               resume_path: str, question_answers: dict,
               cover_letter_pdf: str = "") -> ApplicationResult:
        """Submit application via Workable widget API."""
        url = f"https://apply.workable.com/api/v1/widget/accounts/{job.ats_board_token}/jobs/{job.ats_job_id}/candidates"

        payload = {
            "firstname": candidate.get("first_name", ""),
            "lastname": candidate.get("last_name", ""),
            "email": candidate.get("email", ""),
            "sourced": False,  # Applied, not sourced
        }

        if candidate.get("phone"):
            payload["phone"] = candidate["phone"]
        if cover_letter:
            payload["cover_letter"] = cover_letter

        # Resume as base64
        resume = Path(resume_path) if resume_path else None
        if resume and resume.exists():
            with open(resume, "rb") as f:
                payload["resume"] = {
                    "name": "Amane_Dias_CV.pdf",
                    "data": base64.b64encode(f.read()).decode(),
                }

        # Custom answers
        if question_answers:
            payload["answers"] = question_answers

        try:
            data = json.dumps(payload).encode()
            req = urllib.request.Request(
                url,
                data=data,
                headers={
                    "Content-Type": "application/json",
                    "User-Agent": "AmaneJobBot/2.0",
                },
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=30) as resp:
                response_text = resp.read().decode()
                status_code = resp.status

            if status_code in (200, 201):
                return ApplicationResult(
                    success=True,
                    method="api_workable",
                    message=f"Application submitted to {job.company} via Workable API",
                    response_data={"status": status_code, "response": response_text[:500]},
                )
            else:
                return ApplicationResult(
                    success=False,
                    method="api_workable",
                    message=f"Workable API returned {status_code}",
                    response_data={"status": status_code, "response": response_text[:500]},
                )

        except urllib.error.HTTPError as e:
            error_body = e.read().decode() if e.fp else ""
            return ApplicationResult(
                success=False,
                method="api_workable",
                message=f"Workable API error {e.code}: {error_body[:200]}",
                response_data={"status": e.code, "error": error_body[:500]},
            )
        except Exception as e:
            return ApplicationResult(
                success=False,
                method="api_workable",
                message=f"Workable submit failed: {e}",
                response_data={"error": str(e)},
            )
