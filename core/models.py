from dataclasses import dataclass, field
from datetime import date
import hashlib


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
    score_reason: str = ""
    cover_letter: str = ""
    status: str = "new"

    @property
    def id(self) -> str:
        return hashlib.sha256(self.url.encode()).hexdigest()[:16]
