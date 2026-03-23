"""
Base class and shared types for ATS API integrations.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from core.models import Job


@dataclass
class ApplicationResult:
    success: bool
    method: str  # "api_greenhouse", "api_lever", "api_workable", "browser", "email"
    message: str
    response_data: dict


class ATSApplicant(ABC):
    """Base class for ATS platform API integrations."""

    platform_name: str = ""

    @abstractmethod
    def can_apply(self, job: Job) -> bool:
        """Check if this applicant can handle the given job."""
        ...

    @abstractmethod
    def fetch_questions(self, job: Job) -> list[dict]:
        """Fetch custom screening questions for the job posting."""
        ...

    @abstractmethod
    def submit(self, job: Job, candidate: dict, cover_letter: str,
               resume_path: str, question_answers: dict,
               cover_letter_pdf: str = "") -> ApplicationResult:
        """Submit an application. Returns result with success/failure info."""
        ...
