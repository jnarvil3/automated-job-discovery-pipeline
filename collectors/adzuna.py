import logging
import urllib.parse
import os
import re
import requests
from collectors.base import BaseCollector
from core.models import Job

log = logging.getLogger(__name__)

API_BASE = "https://api.adzuna.com/v1/api/jobs/de/search"

# Search queries — each combines a role type with a field
SEARCHES = [
    "working student finance",
    "working student sustainability",
    "working student renewable energy",
    "working student controlling",
    "working student FP&A",
    "werkstudent finance",
    "werkstudent nachhaltigkeit",
    "intern finance germany",
    "intern sustainability germany",
    "intern renewable energy germany",
    "internship controlling germany",
    "praktikum finance",
    "working student back office",
    "working student marketing",
    "werkstudent klimaschutz",
]


class AdzunaCollector(BaseCollector):
    def __init__(self):
        self.app_id = os.environ.get("ADZUNA_APP_ID", "")
        self.app_key = os.environ.get("ADZUNA_APP_KEY", "")

    def collect(self) -> list[Job]:
        if not self.app_id or not self.app_key:
            log.warning("ADZUNA_APP_ID / ADZUNA_APP_KEY not set — skipping")
            return []

        jobs: list[Job] = []
        seen_urls: set[str] = set()
        session = requests.Session()
        session.headers["User-Agent"] = "AmaneJobBot/1.0"

        for query in SEARCHES:
            encoded_query = urllib.parse.quote(query)
            for page in range(1, 3):  # pages 1-2
                try:
                    url = f"{API_BASE}/{page}?app_id={self.app_id}&app_key={self.app_key}&what={encoded_query}&results_per_page=20"
                    resp = session.get(url, timeout=15)
                    resp.raise_for_status()
                    data = resp.json()

                    results = data.get("results", [])
                    if not results:
                        break  # no more results for this query

                    for item in results:
                        link = item.get("redirect_url", "")
                        if not link or link in seen_urls:
                            continue
                        seen_urls.add(link)

                        desc = item.get("description", "")
                        desc = re.sub(r"<[^>]+>", " ", desc)
                        desc = re.sub(r"\s+", " ", desc).strip()

                        jobs.append(Job(
                            title=item.get("title", "Unknown"),
                            company=item.get("company", {}).get("display_name", "Unknown"),
                            location=item.get("location", {}).get("display_name", "Germany"),
                            description=desc[:2000],
                            url=link,
                            source="adzuna",
                            posted_date=item.get("created", ""),
                        ))
                except Exception as e:
                    log.warning("Error for '%s' page %d: %s", query, page, e)
                    break  # stop paginating this query on error

        log.info("Collected %d jobs from %d searches", len(jobs), len(SEARCHES))
        return jobs
