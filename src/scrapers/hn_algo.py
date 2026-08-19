from __future__ import annotations

import httpx
from tenacity import retry, stop_after_attempt, wait_exponential_jitter, retry_if_exception_type
from typing import List
from .base import BaseScraper, JobListing


class HNAlgoliaScraper(BaseScraper):
    source_name: str = "hacker_news_who_is_hiring"

    def __init__(self, query_tags: str = "story,who_is_hiring", hits_per_page: int = 200):
        self.url = "https://hn.algolia.com/api/v1/search_by_date"
        self.params = {
            "tags": query_tags,
            "hitsPerPage": hits_per_page,
        }

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential_jitter(initial=2, max=30),
        retry=retry_if_exception_type((httpx.HTTPError, httpx.TimeoutException)),
        reraise=True,
    )
    async def _get(self, client: httpx.AsyncClient) -> dict:
        resp = await client.get(self.url, params=self.params)
        resp.raise_for_status()
        return resp.json()

    @staticmethod
    def _clean(text: str | None) -> str | None:
        if not text:
            return None
        return " ".join(text.split())

    async def fetch(self, session) -> List[JobListing]:
        data = await self._get(session)
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