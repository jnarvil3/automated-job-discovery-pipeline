"""
Detect which ATS platform a job URL points to and extract identifiers.
"""

import re


# URL patterns for common ATS platforms
ATS_PATTERNS = [
    # Greenhouse: boards.greenhouse.io/{board}/jobs/{id} or job-boards.greenhouse.io/...
    (
        "greenhouse",
        re.compile(
            r"(?:boards|job-boards)\.greenhouse\.io/(?P<board>[^/]+)/jobs/(?P<job_id>\d+)",
            re.IGNORECASE,
        ),
    ),
    # Lever: jobs.lever.co/{company}/{posting_id}
    (
        "lever",
        re.compile(
            r"jobs\.lever\.co/(?P<board>[^/]+)/(?P<job_id>[a-f0-9-]+)",
            re.IGNORECASE,
        ),
    ),
    # Workable: apply.workable.com/{company}/j/{shortcode} or {company}.workable.com/j/{shortcode}
    (
        "workable",
        re.compile(
            r"(?:apply\.workable\.com/(?P<board>[^/]+)|(?P<board2>[^./]+)\.workable\.com)/j/(?P<job_id>[A-Za-z0-9]+)",
            re.IGNORECASE,
        ),
    ),
    # Personio: {company}.jobs.personio.de/job/{id} or {company}.jobs.personio.com/job/{id}
    (
        "personio",
        re.compile(
            r"(?P<board>[^./]+)\.jobs\.personio\.(?:de|com)/job/(?P<job_id>\d+)",
            re.IGNORECASE,
        ),
    ),
    # SmartRecruiters: jobs.smartrecruiters.com/{company}/{id}
    (
        "smartrecruiters",
        re.compile(
            r"jobs\.smartrecruiters\.com/(?P<board>[^/]+)/(?P<job_id>\d+)",
            re.IGNORECASE,
        ),
    ),
    # BambooHR: {company}.bamboohr.com/careers/{id}
    (
        "bamboohr",
        re.compile(
            r"(?P<board>[^./]+)\.bamboohr\.com/(?:careers|jobs)/(?P<job_id>\d+)",
            re.IGNORECASE,
        ),
    ),
    # Ashby: jobs.ashbyhq.com/{company}/{id}
    (
        "ashby",
        re.compile(
            r"jobs\.ashbyhq\.com/(?P<board>[^/]+)/(?P<job_id>[a-f0-9-]+)",
            re.IGNORECASE,
        ),
    ),
]

# HTML markers for ATS platforms (checked when URL patterns don't match)
ATS_HTML_MARKERS = [
    ("greenhouse", re.compile(r"greenhouse\.io|data-greenhouse", re.IGNORECASE)),
    ("lever", re.compile(r"lever\.co|data-lever", re.IGNORECASE)),
    ("workable", re.compile(r"workable\.com/widget", re.IGNORECASE)),
    ("personio", re.compile(r"personio\.de|personio\.com", re.IGNORECASE)),
    ("smartrecruiters", re.compile(r"smartrecruiters\.com", re.IGNORECASE)),
]


def detect_ats(url: str, html: str = "") -> tuple[str, str, str]:
    """
    Detect the ATS platform from a job URL and optionally page HTML.

    Returns:
        (platform_name, job_id, board_token)
        platform_name is "" if no ATS detected.
    """
    # First: try URL pattern matching (most reliable)
    for platform, pattern in ATS_PATTERNS:
        match = pattern.search(url)
        if match:
            groups = match.groupdict()
            job_id = groups.get("job_id", "")
            board = groups.get("board", "") or groups.get("board2", "")
            return platform, job_id, board

    # Fallback: check HTML for ATS markers (when job pages embed ATS iframes)
    if html:
        for platform, marker in ATS_HTML_MARKERS:
            if marker.search(html):
                # Try to extract IDs from the HTML for this platform
                job_id, board = _extract_ids_from_html(platform, html)
                return platform, job_id, board

    return "", "", ""


def _extract_ids_from_html(platform: str, html: str) -> tuple[str, str]:
    """Try to extract job ID and board token from HTML content for a known ATS."""
    if platform == "greenhouse":
        # Look for greenhouse embed: data-greenhouse-token="..." or gh_jid=...
        token_match = re.search(r'(?:data-greenhouse-token|gh_token)[="][\s"]*([^"&\s]+)', html)
        jid_match = re.search(r'(?:gh_jid|job_id)[="][\s"]*(\d+)', html)
        board = token_match.group(1) if token_match else ""
        job_id = jid_match.group(1) if jid_match else ""
        return job_id, board

    if platform == "lever":
        # Look for lever posting ID in embed URL
        match = re.search(r'jobs\.lever\.co/([^/]+)/([a-f0-9-]+)', html)
        if match:
            return match.group(2), match.group(1)

    return "", ""
