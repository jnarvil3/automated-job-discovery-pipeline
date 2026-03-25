from dataclasses import dataclass, field
from datetime import date
import hashlib
from urllib.parse import urlparse, parse_qs, urlencode, urlunparse


def normalize_url(url: str) -> str:
    """Strip tracking params and trailing slashes for consistent dedup."""
    parsed = urlparse(url)
    params = parse_qs(parsed.query)
    for key in list(params):
        if key.startswith("utm_") or key in ("ref", "source", "fbclid", "mc_cid", "mc_eid"):
            del params[key]
    clean_query = urlencode(params, doseq=True)
    clean_path = parsed.path.rstrip("/") or "/"
    return urlunparse(parsed._replace(query=clean_query, path=clean_path))


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
    posted_date: str = ""  # ISO date when the job was originally posted

    @property
    def id(self) -> str:
        return hashlib.sha256(normalize_url(self.url).encode()).hexdigest()[:16]
