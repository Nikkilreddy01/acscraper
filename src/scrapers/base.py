from __future__ import annotations

from abc import ABC, abstractmethod
from typing import List, Optional
from pydantic import BaseModel


def timeframe_to_days(timeframe: str | None) -> Optional[int]:
    """Convert timeframe string (e.g. 24h, 7d, 1m, 3m, 6m, 1y) to days."""
    if not timeframe or timeframe.lower() in ("all", "any", "none"):
        return None
    tf = timeframe.lower().strip()
    mapping = {
        "24h": 1,
        "1d": 1,
        "7d": 7,
        "1w": 7,
        "1m": 30,
        "1month": 30,
        "3m": 90,
        "3months": 90,
        "6m": 180,
        "6months": 180,
        "1y": 365,
        "1year": 365,
    }
    return mapping.get(tf)


def timeframe_to_seconds(timeframe: str | None) -> Optional[int]:
    """Convert timeframe string to seconds."""
    days = timeframe_to_days(timeframe)
    return days * 86400 if days else None


class JobListing(BaseModel):
    id: str
    title: str
    company: str | None = None
    location: str | None = None
    url: str
    source: str
    posted_at: str | None = None
    description_snippet: str | None = None
    salary: str | None = None


class BaseScraper(ABC):
    source_name: str = "base"

    @abstractmethod
    async def fetch(
        self,
        session=None,
        timeframe: str = "all",
        query: str | None = None,
        location: str | None = None,
    ) -> List[JobListing]:
        raise NotImplementedError
