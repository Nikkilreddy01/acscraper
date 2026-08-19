from abc import ABC, abstractmethod
from typing import List
from pydantic import BaseModel


class JobListing(BaseModel):
    id: str
    title: str
    company: str | None = None
    location: str | None = None
    url: str
    source: str
    posted_at: str | None = None
    description_snippet: str | None = None


class BaseScraper(ABC):
    source_name: str = "base"

    @abstractmethod
    async def fetch(self, session) -> List[JobListing]:
        raise NotImplementedError