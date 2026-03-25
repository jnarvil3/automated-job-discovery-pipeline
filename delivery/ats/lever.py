"""
Lever Postings API integration.

Docs: https://github.com/lever/postings-api
Public postings API — requires an API key that is sometimes embedded in the career page JS.
Rate limit: 2 requests/second.
"""

import json
import time
import urllib.request
import urllib.parse
from io import BytesIO
from pathlib import Path
from core.models import Job
from delivery.ats.base import ATSApplicant, ApplicationResult


class LeverApplicant(ATSApplicant):
    platform_name = "lever"

    def can_apply(self, job: Job) -> bool:
        # Lever requires board token (company slug) and job ID
        return bool(job.ats_platform == "lever" and job.ats_board_token and job.ats_job_id)

    def fetch_questions(self, job: Job) -> list[dict]:
        """Fetch posting details including custom questions."""
        url = f"https://api.lever.co/v0/postings/{job.ats_board_token}/{job.ats_job_id}"
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "AmaneJobBot/2.0"})
            with urllib.request.urlopen(req, timeout=10) as resp:
                data = json.loads(resp.read().decode())
            # Lever returns questions in the 'lists' field
            return data.get("lists", [])
        except Exception as e:
            print(f"    [lever] Failed to fetch posting details: {e}")
            return []

    def submit(self, job: Job, candidate: dict, cover_letter: str,
               resume_path: str, question_answers: dict,
               cover_letter_pdf: str = "") -> ApplicationResult:
        """Submit application via Lever postings API."""
        url = f"https://api.lever.co/v0/postings/{job.ats_board_token}/{job.ats_job_id}/apply"

        # Lever uses multipart form data
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
        full_name = f"{candidate.get('first_name', '')} {candidate.get('last_name', '')}".strip()
        add_field("name", full_name)
        add_field("email", candidate.get("email", ""))

        # Optional fields
        if candidate.get("phone"):
            add_field("phone", candidate["phone"])
        if candidate.get("linkedin_url"):
            add_field("urls[LinkedIn]", candidate["linkedin_url"])
        if cover_letter:
            add_field("comments", cover_letter)

        # GDPR consent (required for EU)
        add_field("consent[marketing]", "false")
        add_field("consent[store]", "true")

        # Resume
        resume = Path(resume_path) if resume_path else None
        if resume and resume.exists():
            with open(resume, "rb") as f:
                full_name_r = f"{candidate.get('first_name', '')}_{candidate.get('last_name', '')}".replace(' ', '_')
                add_file("resume", f"{full_name_r}_CV.pdf", f.read())

        # Custom answers
        for key, value in question_answers.items():
            add_field(f"cards[{key}]", str(value))

        body.write(f"--{boundary}--\r\n".encode())

        # Respect rate limit
        time.sleep(0.5)

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
                    method="api_lever",
                    message=f"Application submitted to {job.company} via Lever API",
                    response_data={"status": status_code, "response": response_text[:500]},
                )
            else:
                return ApplicationResult(
                    success=False,
                    method="api_lever",
                    message=f"Lever API returned {status_code}",
                    response_data={"status": status_code, "response": response_text[:500]},
                )

        except urllib.error.HTTPError as e:
            error_body = e.read().decode() if e.fp else ""
            # Handle rate limiting
            if e.code == 429:
                return ApplicationResult(
                    success=False,
                    method="api_lever",
                    message="Lever API rate limited (429) — will retry next run",
                    response_data={"status": 429},
                )
            return ApplicationResult(
                success=False,
                method="api_lever",
                message=f"Lever API error {e.code}: {error_body[:200]}",
                response_data={"status": e.code, "error": error_body[:500]},
            )
        except Exception as e:
            return ApplicationResult(
                success=False,
                method="api_lever",
                message=f"Lever submit failed: {e}",
                response_data={"error": str(e)},
            )
