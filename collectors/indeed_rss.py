import logging
import time
import feedparser
import re
from collectors.base import BaseCollector
from core.models import Job

log = logging.getLogger(__name__)


class IndeedRSSCollector(BaseCollector):
    def __init__(self, feed_urls: list[str]):
        self.feed_urls = feed_urls

    def collect(self) -> list[Job]:
        jobs: list[Job] = []
        seen_urls: set[str] = set()

        for i, url in enumerate(self.feed_urls):
            if i > 0:
                time.sleep(1.5)
            try:
                feed = feedparser.parse(url)
                for entry in feed.entries:
                    link = entry.get("link", "")
                    if not link or link in seen_urls:
                        continue
                    seen_urls.add(link)

                    # Clean HTML from description
                    desc = entry.get("summary", "")
                    desc = re.sub(r"<[^>]+>", " ", desc)
                    desc = re.sub(r"\s+", " ", desc).strip()

                    jobs.append(Job(
                        title=entry.get("title", "Unknown"),
                        company=self._extract_company(entry),
                        location=self._extract_location(entry),
                        description=desc,
                        url=link,
                        source="indeed",
                    ))
            except Exception as e:
                log.warning("Error fetching %s: %s", url, e)

        log.info("Collected %d jobs from %d feeds", len(jobs), len(self.feed_urls))
        return jobs

    def _extract_company(self, entry) -> str:
        # Indeed RSS sometimes puts company in source or author
        if hasattr(entry, "source") and hasattr(entry.source, "title"):
            return entry.source.title
        return entry.get("author", "Unknown")

    def _extract_location(self, entry) -> str:
        # Indeed sometimes includes location in the title or custom fields
        for key in ("georss_point", "location"):
            if key in entry:
                return entry[key]
        return "Germany"
