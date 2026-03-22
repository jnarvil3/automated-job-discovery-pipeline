import urllib.request
import json
from collectors.base import BaseCollector
from core.models import Job

API_URL = "https://www.arbeitnow.com/api/job-board-api"

# Keywords that signal a relevant role
ROLE_KEYWORDS = {"working student", "werkstudent", "intern", "internship", "praktikum"}
FIELD_KEYWORDS = {
    "finance", "fp&a", "financial planning", "controlling", "sustainability",
    "renewable", "energy", "climate", "back office", "backoffice", "marketing",
}


class ArbeitnowCollector(BaseCollector):
    def collect(self) -> list[Job]:
        jobs: list[Job] = []
        page = 1

        while page <= 5:  # cap at 5 pages to avoid runaway
            try:
                url = f"{API_URL}?page={page}"
                req = urllib.request.Request(url, headers={"User-Agent": "AmaneJobBot/1.0"})
                with urllib.request.urlopen(req, timeout=15) as resp:
                    data = json.loads(resp.read().decode())
            except Exception as e:
                print(f"[arbeitnow] Error fetching page {page}: {e}")
                break

            listings = data.get("data", [])
            if not listings:
                break

            for item in listings:
                title = item.get("title", "").lower()
                desc = item.get("description", "").lower()
                tags = " ".join(item.get("tags", [])).lower()
                combined = f"{title} {desc} {tags}"

                # Filter: must match a role keyword AND a field keyword
                has_role = any(kw in combined for kw in ROLE_KEYWORDS)
                has_field = any(kw in combined for kw in FIELD_KEYWORDS)

                if has_role and has_field:
                    jobs.append(Job(
                        title=item.get("title", "Unknown"),
                        company=item.get("company_name", "Unknown"),
                        location=item.get("location", "Germany"),
                        description=item.get("description", "")[:2000],
                        url=item.get("url", ""),
                        source="arbeitnow",
                    ))

            if not data.get("links", {}).get("next"):
                break
            page += 1

        print(f"[arbeitnow] Collected {len(jobs)} matching jobs")
        return jobs
