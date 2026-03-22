from abc import ABC, abstractmethod
from core.models import Job


class BaseCollector(ABC):
    @abstractmethod
    def collect(self) -> list[Job]:
        """Fetch jobs from the source and return normalized Job objects."""
        ...
