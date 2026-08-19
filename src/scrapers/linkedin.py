from __future__ import annotations

import os
import time
import re
import asyncio
import logging
from datetime import datetime, timezone
from typing import Optional

from curl_cffi.requests import AsyncSession

from ._stealth import stealth_session, STEALTH_HEADERS, _jittered_sleep
from .base import BaseScraper, JobListing

logger = logging.getLogger(__name__)

TITLE_RE = re.compile(r'<h3[^>]*class="[^"]*base-search-card__title[^"]*"[^>]*>(.*?)</h3>', re.S)
COMPANY_RE = re.compile(r'<h4[^>]*class="[^"]*base-search-card__subtitle[^"]*"[^>]*>.*?<a[^>]*>(.*?)</a>', re.S)
LOCATION_RE = re.compile(r'<span[^>]*class="[^"]*job-search-card__location[^"]*"[^>]*>(.*?)</span>', re.S)
URL_RE = re.compile(r'<a[^>]*class="[^"]*base-card__full-link[^"]*"[^>]+href="([^"]+)"', re.S)
URN_RE = re.compile(r'urn:li:jobPosting:(\d+)')
DATE_RE = re.compile(r'<time[^>]+datetime="([^"]+)"')


class LinkedInScraper(BaseScraper):
    source_name = "linkedin"

    def __init__(
        self,
        keywords: str = "software engineer",
        location: str = "Remote",
        max_pages: int = 1,
        proxy: Optional[str] = None,
    ):
        self.keywords = keywords
        self.location = location
        self.max_pages = max_pages
        self.proxy = proxy or os.environ.get("SCRAPER_PROXY")

    async def fetch(self, _session=None) -> list[JobListing]:
        jobs: list[JobListing] = []
        async with stealth_session(impersonate="chrome145", proxy=self.proxy) as client:
            for page in range(self.max_pages):
                start = page * 25
                url = "https://www.linkedin.com/jobs-guest/jobs/api/seeMoreJobPostings/search"
                params = {
                    "keywords": self.keywords,
                    "location": self.location,
                    "start": start,
                    "count": 25,
                }
                try:
                    resp = await client.get(
                        url,
                        params=params,
                        headers={
                            "x-restli-protocol-version": "2.0.0",
                            **STEALTH_HEADERS,
                        },
                        timeout=30,
                    )
                    resp.raise_for_status()
                    html = resp.text
                    jobs.extend(self._parse(html))
                except Exception as exc:
                    logger.warning("LinkedIn page %d failed: %s", page, exc)
                    if not jobs:
                        raise

                if page < self.max_pages - 1:
                    _jittered_sleep(2, 5)

        return jobs

    @staticmethod
    def _clean(s: str | None) -> str | None:
        if not s:
            return None
        return " ".join(s.split())

    def _parse(self, html: str) -> list[JobListing]:
        out: list[JobListing] = []
        cards = re.split(r'<li[^>]*>\s*<div[^>]*class="[^"]*base-search-card', html)
        for card in cards[1:]:
            urn_m = URN_RE.search(card)
            job_id = urn_m.group(1) if urn_m else str(abs(hash(card)))[:12]

            title = self._clean(TITLE_RE.search(card).group(1) if TITLE_RE.search(card) else None)
            if not title:
                continue

            company_m = COMPANY_RE.search(card)
            company = self._clean(company_m.group(1) if company_m else None)

            loc_m = LOCATION_RE.search(card)
            location = self._clean(loc_m.group(1) if loc_m else None)

            url_m = URL_RE.search(card)
            url = url_m.group(1) if url_m else f"https://www.linkedin.com/jobs/search/?keywords={self.keywords}"

            date_m = DATE_RE.search(card)
            posted_at = date_m.group(1) if date_m else None

            out.append(JobListing(
                id=f"li_{job_id}",
                title=title,
                company=company,
                location=location,
                url=url,
                source=self.source_name,
                posted_at=posted_at,
                description_snippet=f"{self.keywords} job on LinkedIn",
            ))
        return out