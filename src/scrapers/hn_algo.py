from __future__ import annotations

import time
import httpx
from tenacity import retry, stop_after_attempt, wait_exponential_jitter, retry_if_exception_type
from typing import List, Optional
from .base import BaseScraper, JobListing, timeframe_to_seconds


class HNAlgoliaScraper(BaseScraper):
    source_name: str = "hackernews"

    def __init__(self, query: str = "hiring", hits_per_page: int = 50):
        self.url = "https://hn.algolia.com/api/v1/search"
        self.query = query
        self.hits_per_page = hits_per_page

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential_jitter(initial=2, max=15),
        retry=retry_if_exception_type((httpx.HTTPError, httpx.TimeoutException)),
        reraise=True,
    )
    async def _get(self, client: httpx.AsyncClient, params: dict) -> dict:
        resp = await client.get(self.url, params=params)
        resp.raise_for_status()
        return resp.json()

    @staticmethod
    def _clean(text: str | None) -> str | None:
        if not text:
            return None
        return " ".join(text.split())

    async def fetch(
        self,
        session=None,
        timeframe: str = "all",
        query: str | None = None,
        location: str | None = None,
    ) -> List[JobListing]:
        params = {
            "query": query or self.query,
            "tags": "story",
            "hitsPerPage": self.hits_per_page,
        }
        
        secs = timeframe_to_seconds(timeframe)
        if secs:
            min_ts = int(time.time() - secs)
            params["numericFilters"] = f"created_at_i>={min_ts}"

        if session is None:
            async with httpx.AsyncClient(timeout=15.0) as client:
                data = await self._get(client, params)
        else:
            data = await self._get(session, params)

        hits = data.get("hits", [])
        out: List[JobListing] = []
        for h in hits:
            title = self._clean(h.get("title"))
            if not title:
                continue
            url = h.get("url") or f"https://news.ycombinator.com/item?id={h.get('objectID', '')}"
            company = self._clean(h.get("author"))
            out.append(
                JobListing(
                    id=str(h.get("objectID", "")),
                    title=title,
                    company=company,
                    url=url,
                    source=self.source_name,
                    posted_at=h.get("created_at"),
                    description_snippet=self._clean(h.get("story_text") or h.get("url")),
                )
            )
        return out
