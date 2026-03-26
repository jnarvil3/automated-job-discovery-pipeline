"""
Fetches full job descriptions from URLs and checks for German language requirements.
Also detects ATS platforms and extracts application emails.
"""

import logging
import re
import time
import requests
from concurrent.futures import ThreadPoolExecutor, as_completed
from html.parser import HTMLParser
from core.models import Job
from core.ats_detector import detect_ats

log = logging.getLogger(__name__)

# Module-level session for connection pooling across ThreadPoolExecutor workers
_session = requests.Session()
_session.headers.update({
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml",
    "Accept-Language": "en-US,en;q=0.9",
})

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


ATS_CAREER_PATTERNS = [
    # (url_template, ats_name) — {slug} gets replaced with company slug
    ("https://boards.greenhouse.io/{slug}", "greenhouse"),
    ("https://jobs.lever.co/{slug}", "lever"),
    ("https://{slug}.jobs.personio.de", "personio"),
    ("https://{slug}.recruitee.com", "recruitee"),
    ("https://apply.workable.com/{slug}", "workable"),
    ("https://{slug}.bamboohr.com/careers", "bamboohr"),
]


def _company_to_slugs(company: str) -> list[str]:
    """Generate possible URL slugs from a company name."""
    # Strip common suffixes
    clean = re.sub(r"\s+(SE|AG|GmbH|Inc|Ltd|Corp|LLC|Co|KG|e\.V\.)\.?$", "", company, flags=re.I).strip()
    slugs = []
    # Full name slugged
    slug = re.sub(r"[^a-z0-9]+", "-", clean.lower()).strip("-")
    if slug:
        slugs.append(slug)
    # First word only (many companies use this)
    first = slug.split("-")[0]
    if first and first != slug:
        slugs.append(first)
    return slugs


def _probe_ats_career_pages(company: str) -> tuple[str, str, str]:
    """
    Try known ATS URL patterns for a company to find their real career page.
    Returns (ats_url, ats_platform, raw_html) or ("", "", "") if not found.
    """
    slugs = _company_to_slugs(company)
    for slug in slugs:
        for pattern, ats_name in ATS_CAREER_PATTERNS:
            try:
                probe_url = pattern.format(slug=slug)
                resp = _session.get(probe_url, timeout=8, allow_redirects=True)
                # Verify it's a real career board, not a generic/error page
                final = resp.url
                is_generic = any(final.rstrip("/") == base.rstrip("/") for base in [
                    "https://recruitee.com", "https://www.recruitee.com",
                    "https://boards.greenhouse.io", "https://jobs.lever.co",
                ])
                # Workable redirects bad slugs to /oops
                if "/oops" in final:
                    is_generic = True
                # BambooHR redirects bad slugs to their homepage
                if final.rstrip("/") in ("https://www.bamboohr.com", "https://bamboohr.com"):
                    is_generic = True
                if resp.status_code == 200 and len(resp.text) > 500 and not is_generic:
                    log.info("  Found %s career page: %s", ats_name, probe_url)
                    return resp.url, ats_name, resp.text
            except Exception:
                continue
    return "", "", ""


def fetch_full_description(url: str, company: str = "", max_retries: int = 3) -> tuple[str | None, str, str]:
    """
    Fetch a job URL and extract the visible text content.
    If the URL is an aggregator (Adzuna, etc.) that blocks scraping,
    probes known ATS career page patterns using the company name.

    Returns:
        (text_content, final_url_after_redirects, raw_html)
    """
    for attempt in range(max_retries):
        try:
            resp = _session.get(url, timeout=10)
            if resp.status_code >= 500:
                if attempt < max_retries - 1:
                    time.sleep(2 * (attempt + 1))
                    continue
                break  # fall through to ATS probe
            if resp.status_code in (403, 404):
                break  # aggregator blocking — fall through to ATS probe

            resp.raise_for_status()

            final_url = resp.url
            raw_html = resp.text

            extractor = TextExtractor()
            extractor.feed(raw_html)
            text = extractor.get_text()
            # Clean up whitespace
            text = re.sub(r"\s+", " ", text).strip()
            return text[:3000], final_url, raw_html
        except requests.exceptions.HTTPError:
            break  # fall through to ATS probe
        except (requests.exceptions.Timeout, requests.exceptions.ConnectionError):
            if attempt < max_retries - 1:
                time.sleep(2 * (attempt + 1))
                continue
            break
        except Exception:
            break

    # Aggregator URL failed — try to find the company's real career page
    if company:
        ats_url, ats_name, raw_html = _probe_ats_career_pages(company)
        if ats_url:
            extractor = TextExtractor()
            extractor.feed(raw_html)
            text = re.sub(r"\s+", " ", extractor.get_text()).strip()
            return text[:3000], ats_url, raw_html

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


def _fetch_for_job(job: Job) -> tuple[Job, str | None, str, str]:
    """Fetch full description for a single job. Thread-safe helper."""
    full_text, final_url, raw_html = fetch_full_description(job.url, company=job.company)
    return job, full_text, final_url, raw_html


def enrich_jobs(jobs: list[Job]) -> tuple[list[Job], list[Job]]:
    """
    Fetch full descriptions and filter out jobs requiring German.
    Returns (english_jobs, german_required_jobs).
    """
    english_jobs = []
    german_jobs = []

    # Parallel fetch with staggered submissions to avoid overwhelming servers
    fetch_results: dict[str, tuple[str | None, str, str]] = {}
    with ThreadPoolExecutor(max_workers=5) as pool:
        futures = {}
        for job in jobs:
            futures[pool.submit(_fetch_for_job, job)] = job

        for future in as_completed(futures):
            job, full_text, final_url, raw_html = future.result()
            fetch_results[job.url] = (full_text, final_url, raw_html)

    # Process results sequentially (filtering, ATS detection)
    for i, job in enumerate(jobs):
        log.info("[%d/%d] Checking %s at %s...", i+1, len(jobs), job.title, job.company)

        full_text, final_url, raw_html = fetch_results.get(job.url, (None, job.url, ""))

        # Save original URL before overwriting (needed for ATS fallback detection)
        original_url = job.url

        # Update URL to canonical (post-redirect) URL for consistent ID hashing
        if full_text and final_url != job.url:
            job.url = final_url

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
            # Also try the original pre-redirect URL
            platform, job_id, board_token = detect_ats(original_url)
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
