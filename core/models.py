from dataclasses import dataclass, field
from datetime import date
import hashlib


@dataclass
class Job:
    title: str
    company: str
    location: str
    description: str
    url: str
    source: str  # "indeed", "arbeitnow", "linkedin"
    found_date: str = field(default_factory=lambda: date.today().isoformat())
    score: str = ""  # HIGH, MEDIUM, LOW
    fit_score: int = 0  # 1-10 numeric fit ranking
    score_reason: str = ""
    cover_letter: str = ""
    status: str = "new"  # new, applying, auto_applied, apply_failed, apply_skipped_captcha, quick_apply, skipped
    apply_email: str = ""  # extracted application email if found
    # ATS detection fields
    ats_platform: str = ""  # greenhouse, lever, workable, personio, smartrecruiters, unknown
    ats_job_id: str = ""  # platform-specific job ID
    ats_board_token: str = ""  # board token for API-based apply
    apply_method: str = ""  # api, browser, email, manual
    apply_attempts: int = 0
    apply_error: str = ""

    @property
    def id(self) -> str:
        return hashlib.sha256(self.url.encode()).hexdigest()[:16]
