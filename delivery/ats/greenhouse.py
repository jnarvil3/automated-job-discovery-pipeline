"""
Greenhouse Job Board API integration.

Docs: https://developers.greenhouse.io/job-board.html
Public board API — no auth required for most boards.
"""

import json
import logging
import urllib.request
import urllib.parse
from io import BytesIO
from pathlib import Path
from core.models import Job
from delivery.ats.base import ATSApplicant, ApplicationResult

log = logging.getLogger(__name__)


API_BASE = "https://boards-api.greenhouse.io/v1/boards"


class GreenhouseApplicant(ATSApplicant):
    platform_name = "greenhouse"

    def can_apply(self, job: Job) -> bool:
        return bool(job.ats_platform == "greenhouse" and job.ats_board_token and job.ats_job_id)

    def fetch_questions(self, job: Job) -> list[dict]:
        """Fetch the job posting to get custom questions."""
        url = f"{API_BASE}/{job.ats_board_token}/jobs/{job.ats_job_id}"
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "AmaneJobBot/2.0"})
            with urllib.request.urlopen(req, timeout=10) as resp:
                data = json.loads(resp.read().decode())
            return data.get("questions", [])
        except Exception as e:
            log.warning("Failed to fetch questions: %s", e)
            return []

    def submit(self, job: Job, candidate: dict, cover_letter: str,
               resume_path: str, question_answers: dict,
               cover_letter_pdf: str = "") -> ApplicationResult:
        """Submit application via Greenhouse board API using multipart form data."""
        url = f"{API_BASE}/{job.ats_board_token}/jobs/{job.ats_job_id}"

        # Build multipart form data
        boundary = "----AmaneJobBotBoundary"
        body = BytesIO()

        def add_field(name: str, value: str):
            body.write(f"--{boundary}\r\n".encode())
            body.write(f'Content-Disposition: form-data; name="{name}"\r\n\r\n'.encode())
            body.write(f"{value}\r\n".encode())

        def add_file(name: str, filename: str, content: bytes, content_type: str = "application/pdf"):
            body.write(f"--{boundary}\r\n".encode())
            body.write(f'Content-Disposition: form-data; name="{name}"; filename="{filename}"\r\n'.encode())
            body.write(f"Content-Type: {content_type}\r\n\r\n".encode())
            body.write(content)
            body.write(b"\r\n")

        # Required fields
        add_field("first_name", candidate.get("first_name", ""))
        add_field("last_name", candidate.get("last_name", ""))
        add_field("email", candidate.get("email", ""))

        # Optional standard fields
        if candidate.get("phone"):
            add_field("phone", candidate["phone"])
        if candidate.get("linkedin_url"):
            add_field("urls[LinkedIn]", candidate["linkedin_url"])

        # Cover letter — upload PDF if available, otherwise send as text
        cl_pdf = Path(cover_letter_pdf) if cover_letter_pdf else None
        if cl_pdf and cl_pdf.exists():
            with open(cl_pdf, "rb") as f:
                full_name = f"{candidate.get('first_name', '')}_{candidate.get('last_name', '')}".replace(' ', '_')
                add_file("cover_letter", f"{full_name}_Cover_Letter.pdf", f.read())
        elif cover_letter:
            add_field("cover_letter", cover_letter)

        # Resume file upload
        resume = Path(resume_path) if resume_path else None
        if resume and resume.exists():
            with open(resume, "rb") as f:
                full_name_r = f"{candidate.get('first_name', '')}_{candidate.get('last_name', '')}".replace(' ', '_')
                add_file("resume", f"{full_name_r}_CV.pdf", f.read())

        # Custom question answers
        for question_id, answer in question_answers.items():
            if isinstance(answer, list):
                for val in answer:
                    add_field(f"questions[{question_id}][]", str(val))
            else:
                add_field(f"questions[{question_id}]", str(answer))

        body.write(f"--{boundary}--\r\n".encode())

        # Submit
        try:
            req = urllib.request.Request(
                url,
                data=body.getvalue(),
                headers={
                    "Content-Type": f"multipart/form-data; boundary={boundary}",
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
                    method="api_greenhouse",
                    message=f"Application submitted to {job.company} via Greenhouse API",
                    response_data={"status": status_code, "response": response_text[:500]},
                )
            else:
                return ApplicationResult(
                    success=False,
                    method="api_greenhouse",
                    message=f"Greenhouse API returned {status_code}",
                    response_data={"status": status_code, "response": response_text[:500]},
                )

        except urllib.error.HTTPError as e:
            error_body = e.read().decode() if e.fp else ""
            return ApplicationResult(
                success=False,
                method="api_greenhouse",
                message=f"Greenhouse API error {e.code}: {error_body[:200]}",
                response_data={"status": e.code, "error": error_body[:500]},
            )
        except Exception as e:
            return ApplicationResult(
                success=False,
                method="api_greenhouse",
                message=f"Greenhouse submit failed: {e}",
                response_data={"error": str(e)},
            )
