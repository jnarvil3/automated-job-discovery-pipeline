import urllib.parse
import re
import requests
from collectors.base import BaseCollector
from core.models import Job

API_BASE = "https://himalayas.app/jobs/api"

# Role-type pre-filter — same keywords as arbeitnow
ROLE_KEYWORDS = {"working student", "werkstudent", "intern", "internship", "praktikum"}

# Searches targeting Amane's fields — Himalayas is remote-focused
SEARCHES = [
    "finance",
    "sustainability",
    "renewable energy",
    "controlling",
    "FP&A",
    "financial analyst",
    "back office",
    "marketing intern",
    "climate",
    "ESG",
]


class HimalayasCollector(BaseCollector):
    def collect(self) -> list[Job]:
        jobs: list[Job] = []
        seen_urls: set[str] = set()
        session = requests.Session()
        session.headers["User-Agent"] = "AmaneJobBot/1.0"

        for query in SEARCHES:
            try:
                encoded_query = urllib.parse.quote(query)
                url = f"{API_BASE}?q={encoded_query}&country=Germany&limit=50"
                resp = session.get(url, timeout=15)
                resp.raise_for_status()
                data = resp.json()

                for item in data.get("jobs", []):
                    link = item.get("applicationLink") or item.get("guid") or ""
                    if not link or link in seen_urls:
                        continue

                    title = item.get("title", "Unknown")
                    # Pre-filter: skip roles that don't match any role keyword
                    if not any(kw in title.lower() for kw in ROLE_KEYWORDS):
                        continue

                    seen_urls.add(link)

                    desc = item.get("excerpt") or item.get("description", "")
                    desc = re.sub(r"<[^>]+>", " ", desc)
                    desc = re.sub(r"\s+", " ", desc).strip()

                    jobs.append(Job(
                        title=title,
                        company=item.get("companyName", "Unknown"),
                        location="Germany (Remote)",
                        description=desc[:2000],
                        url=link,
                        source="himalayas",
                    ))
            except Exception as e:
                print(f"[himalayas] Error for '{query}': {e}")

        print(f"[himalayas] Collected {len(jobs)} jobs from {len(SEARCHES)} searches")
        return jobs
