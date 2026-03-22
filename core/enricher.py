"""
Fetches full job descriptions from URLs and checks for German language requirements.
"""

import re
import urllib.request
from html.parser import HTMLParser
from core.models import Job

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


class TextExtractor(HTMLParser):
    """Extract visible text from HTML."""
    def __init__(self):
        super().__init__()
        self.text_parts = []
        self._skip = False
        self._skip_tags = {"script", "style", "noscript", "header", "footer", "nav"}

    def handle_starttag(self, tag, attrs):
        if tag in self._skip_tags:
            self._skip = True

    def handle_endtag(self, tag):
        if tag in self._skip_tags:
            self._skip = False

    def handle_data(self, data):
        if not self._skip:
            self.text_parts.append(data)

    def get_text(self) -> str:
        return " ".join(self.text_parts)


def fetch_full_description(url: str) -> str | None:
    """Fetch a job URL and extract the visible text content."""
    try:
        req = urllib.request.Request(url, headers={
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Accept": "text/html,application/xhtml+xml",
            "Accept-Language": "en-US,en;q=0.9",
        })
        with urllib.request.urlopen(req, timeout=10) as resp:
            html = resp.read().decode("utf-8", errors="ignore")

        extractor = TextExtractor()
        extractor.feed(html)
        text = extractor.get_text()
        # Clean up whitespace
        text = re.sub(r"\s+", " ", text).strip()
        return text[:5000]  # cap at 5000 chars for scoring
    except Exception:
        return None


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


def enrich_jobs(jobs: list[Job]) -> tuple[list[Job], list[Job]]:
    """
    Fetch full descriptions and filter out jobs requiring German.
    Returns (english_jobs, german_required_jobs).
    """
    english_jobs = []
    german_jobs = []

    for i, job in enumerate(jobs):
        print(f"  [{i+1}/{len(jobs)}] Checking {job.title} at {job.company}...", end=" ")

        full_text = fetch_full_description(job.url)
        if full_text:
            # Update description with richer content for scoring
            job.description = full_text[:3000]

            is_german, reason = requires_german(full_text)
            if is_german:
                print(f"REJECTED — {reason}")
                job.score = "LOW"
                job.score_reason = reason
                german_jobs.append(job)
                continue

        # Also check the original description/title
        is_german, reason = requires_german(f"{job.title} {job.description}")
        if is_german:
            print(f"REJECTED — {reason}")
            job.score = "LOW"
            job.score_reason = reason
            german_jobs.append(job)
            continue

        print("OK")
        english_jobs.append(job)

    return english_jobs, german_jobs
