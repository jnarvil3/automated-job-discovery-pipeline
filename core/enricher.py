"""
Fetches full job descriptions from URLs and checks for German language requirements.
Also detects ATS platforms and extracts application emails.
"""

import logging
import re
import time
import urllib.request
import urllib.error
from html.parser import HTMLParser
from core.models import Job
from core.ats_detector import detect_ats

log = logging.getLogger(__name__)

# Patterns that indicate German is required
GERMAN_REQUIRED_PATTERNS = [
    # English phrases
    r"german\s+(is\s+)?(required|mandatory|necessary|essential|needed|must)",
    r"(fluent|proficient|native|excellent|strong)\s+(in\s+)?german",
    r"(require|need|must have|expect).*german",
    r"german\s+(language\s+)?(skills?|proficiency|knowledge|ability)",
    r"(b1|b2|c1|c2)\s*[\-–]?\s*(level\s+)?(german|deutsch)",
    r"german\s+(b1|b2|c1|c2)",
    r"business[- ]level\s+german",
    # German phrases (these appear even in English-written postings)
    r"deutschkenntnisse",
    r"deutsch\s+(flie[sß]end|verhandlungssicher|erforderlich|zwingend|muttersprachlich|mindestens)",
    r"flie[sß]end(e[srn]?)?\s+deutsch",
    r"sehr\s+gute[rn]?\s+deutschkenntnisse",
    r"verhandlungssicher(e[srn]?)?\s+(in\s+)?deutsch",
    r"muttersprach(e|lich|niveau).*deutsch",
    r"deutsch.*muttersprach",
    r"sprache.*deutsch.*erforderlich",
    r"gute\s+deutschkenntnisse",
]

COMPILED_PATTERNS = [re.compile(p, re.IGNORECASE) for p in GERMAN_REQUIRED_PATTERNS]

# Pattern to extract application/HR emails from job descriptions
EMAIL_PATTERN = re.compile(
    r"(?:apply|application|bewerbung|send|submit|contact|hr|recruiting|talent|career|jobs)"
    r"[^@\n]{0,30}?"
    r"([\w.+-]+@[\w-]+\.[\w.-]+)",
    re.IGNORECASE,
)
# Fallback: any email in the page that looks like HR/recruiting
HR_EMAIL_PATTERN = re.compile(
    r"\b((?:hr|recruiting|talent|career|jobs|apply|bewerbung|people|hiring)"
    r"[\w.+-]*@[\w-]+\.[\w.-]+)\b",
    re.IGNORECASE,
)


class TextExtractor(HTMLParser):
    """Extract visible text from HTML."""
    def __init__(self):
        super().__init__()
        self.text_parts = []
        self._skip_depth = 0
        self._skip_tags = {"script", "style", "noscript", "header", "footer", "nav"}

    def handle_starttag(self, tag, attrs):
        if tag in self._skip_tags:
            self._skip_depth += 1

    def handle_endtag(self, tag):
        if tag in self._skip_tags and self._skip_depth > 0:
            self._skip_depth -= 1

    def handle_data(self, data):
        if self._skip_depth == 0:
            self.text_parts.append(data)

    def get_text(self) -> str:
        return " ".join(self.text_parts)


def fetch_full_description(url: str, max_retries: int = 3) -> tuple[str | None, str, str]:
    """
    Fetch a job URL and extract the visible text content.
    Retries on timeouts and 5xx errors with exponential backoff.

    Returns:
        (text_content, final_url_after_redirects, raw_html)
    """
    headers = {
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml",
        "Accept-Language": "en-US,en;q=0.9",
    }

    for attempt in range(max_retries):
        try:
            req = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(req, timeout=10) as resp:
                final_url = resp.url  # URL after redirects
                html = resp.read().decode("utf-8", errors="ignore")

            extractor = TextExtractor()
            extractor.feed(html)
            text = extractor.get_text()
            # Clean up whitespace
            text = re.sub(r"\s+", " ", text).strip()
            return text[:5000], final_url, html
        except urllib.error.HTTPError as e:
            if e.code >= 500 and attempt < max_retries - 1:
                time.sleep(2 * (attempt + 1))
                continue
            return None, url, ""
        except (TimeoutError, urllib.error.URLError) as e:
            if attempt < max_retries - 1:
                time.sleep(2 * (attempt + 1))
                continue
            return None, url, ""
        except Exception:
            return None, url, ""

    return None, url, ""


def requires_german(text: str) -> tuple[bool, str]:
    """Check if text contains indicators that German language is required."""
    for pattern in COMPILED_PATTERNS:
        match = pattern.search(text)
        if match:
            # Get surrounding context for the reason
            start = max(0, match.start() - 40)
            end = min(len(text), match.end() + 40)
            context = text[start:end].strip()
            return True, f"German required: '...{context}...'"
    return False, ""


def extract_apply_email(text: str) -> str:
    """Try to extract an application email from job description text."""
    # First try: email near apply/application keywords
    match = EMAIL_PATTERN.search(text)
    if match:
        return match.group(1).lower()
    # Fallback: any email that looks like HR/recruiting
    match = HR_EMAIL_PATTERN.search(text)
    if match:
        return match.group(1).lower()
    return ""


def enrich_jobs(jobs: list[Job]) -> tuple[list[Job], list[Job]]:
    """
    Fetch full descriptions and filter out jobs requiring German.
    Returns (english_jobs, german_required_jobs).
    """
    english_jobs = []
    german_jobs = []

    for i, job in enumerate(jobs):
        log.info("[%d/%d] Checking %s at %s...", i+1, len(jobs), job.title, job.company)

        full_text, final_url, raw_html = fetch_full_description(job.url)
        if full_text:
            # Update description with richer content for scoring
            job.description = full_text[:3000]

            is_german, reason = requires_german(full_text)
            if is_german:
                log.info("  REJECTED — %s", reason)
                job.score = "LOW"
                job.score_reason = reason
                german_jobs.append(job)
                continue

            # Try to extract an application email
            job.apply_email = extract_apply_email(full_text)

        # Detect ATS platform from final URL (after redirects) and page HTML
        platform, job_id, board_token = detect_ats(final_url, raw_html)
        if not platform:
            # Also try the original URL
            platform, job_id, board_token = detect_ats(job.url)
        job.ats_platform = platform
        job.ats_job_id = job_id
        job.ats_board_token = board_token

        # Also check the original description/title
        is_german, reason = requires_german(f"{job.title} {job.description}")
        if is_german:
            log.info("  REJECTED — %s", reason)
            job.score = "LOW"
            job.score_reason = reason
            german_jobs.append(job)
            continue

        ats_note = f" [{job.ats_platform}]" if job.ats_platform else ""
        email_note = f" (apply: {job.apply_email})" if job.apply_email else ""
        log.info("  OK%s%s", ats_note, email_note)
        english_jobs.append(job)

    return english_jobs, german_jobs
