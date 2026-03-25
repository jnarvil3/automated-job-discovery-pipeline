import logging
import urllib.parse
import requests
from collectors.base import BaseCollector
from core.models import Job

log = logging.getLogger(__name__)

API_URL = "https://www.arbeitnow.com/api/job-board-api"

# Targeted search queries — let the API do the heavy lifting
SEARCHES = [
    "working student finance",
    "working student sustainability",
    "working student controlling",
    "working student renewable energy",
    "working student FP&A",
    "working student back office",
    "working student marketing",
    "werkstudent finance",
    "werkstudent nachhaltigkeit",
    "werkstudent controlling",
    "intern finance",
    "intern sustainability",
    "intern renewable energy",
    "praktikum finance",
    "praktikum nachhaltigkeit",
]

# Client-side safety net keywords
ROLE_KEYWORDS = {"working student", "werkstudent", "intern", "internship", "praktikum"}
FIELD_KEYWORDS = {
    "finance", "fp&a", "financial planning", "controlling", "sustainability",
    "renewable", "energy", "climate", "back office", "backoffice", "marketing",
}


class ArbeitnowCollector(BaseCollector):
    def collect(self) -> list[Job]:
        jobs: list[Job] = []
        seen_urls: set[str] = set()
        session = requests.Session()
        session.headers["User-Agent"] = "AmaneJobBot/1.0"

        for query in SEARCHES:
            encoded_query = urllib.parse.quote(query)
            for page in range(1, 4):  # pages 1-3 per query
                try:
                    url = f"{API_URL}?search={encoded_query}&page={page}"
                    resp = session.get(url, timeout=15)
                    resp.raise_for_status()
                    data = resp.json()
                except Exception as e:
                    log.warning("Error for '%s' page %d: %s", query, page, e)
                    break

                listings = data.get("data", [])
                if not listings:
                    break

                for item in listings:
                    item_url = item.get("url", "")
                    if not item_url or item_url in seen_urls:
                        continue

                    title_lower = item.get("title", "").lower()
                    desc = item.get("description", "").lower()
                    tags = " ".join(item.get("tags", [])).lower()
                    combined = f"{title_lower} {desc} {tags}"

                    # Safety net: still require role + field keyword match
                    has_role = any(kw in combined for kw in ROLE_KEYWORDS)
                    has_field = any(kw in combined for kw in FIELD_KEYWORDS)

                    if has_role and has_field:
                        seen_urls.add(item_url)
                        jobs.append(Job(
                            title=item.get("title", "Unknown"),
                            company=item.get("company_name", "Unknown"),
                            location=item.get("location", "Germany"),
                            description=item.get("description", "")[:2000],
                            url=item_url,
                            source="arbeitnow",
                            posted_date=item.get("created_at", ""),
                        ))

                if not data.get("links", {}).get("next"):
                    break

        log.info("Collected %d jobs from %d searches", len(jobs), len(SEARCHES))
        return jobs
