from __future__ import annotations

import json
import logging
import re
from typing import Optional

from curl_cffi.requests import AsyncSession

from ._stealth import stealth_session, STEALTH_HEADERS, _jittered_sleep, launch_browser
from .base import BaseScraper, JobListing

logger = logging.getLogger(__name__)

UA = STEALTH_HEADERS["User-Agent"]


def _clean(s: Optional[str]) -> Optional[str]:
    if not s:
        return None
    return " ".join(s.split())


def _parse_indeed_mosaic(html: str) -> list[JobListing]:
    """Parse Indeed's embedded JSON mosaic-provider-jobcards data."""
    out: list[JobListing] = []
    
    # Extract mosaicProviderJobCards JSON
    m = re.search(
        r'window\.mosaic\.providerData\[["\']mosaic-provider-jobcards["\']\]\s*=\s*({.*?});\s*</script>',
        html,
        re.S,
    )
    if not m:
        m = re.search(
            r'window\.mosaic\.providerData\[["\']mosaic-provider-jobcards["\']\]\s*=\s*({.*?});',
            html,
            re.S,
        )

    if m:
        try:
            data = json.loads(m.group(1))
            results = (
                data.get("metaData", {})
                .get("mosaicProviderJobCardsModel", {})
                .get("results", [])
            )
            for idx, item in enumerate(results):
                title = _clean(item.get("displayTitle") or item.get("title"))
                if not title:
                    continue
                jobkey = item.get("jobkey") or str(idx)
                company = _clean(item.get("company"))
                location = _clean(item.get("formattedLocation") or item.get("jobLocationCity"))
                snippet = _clean(item.get("snippet"))
                
                # Check salary if present
                salary_model = item.get("salarySnippet") or {}
                salary_text = _clean(salary_model.get("text")) if isinstance(salary_model, dict) else None
                if salary_text:
                    title = f"{title} ({salary_text})"

                url = f"https://www.indeed.com/viewjob?jk={jobkey}" if jobkey else "https://www.indeed.com"
                
                out.append(
                    JobListing(
                        id=f"indeed_{jobkey}",
                        title=title,
                        company=company,
                        location=location,
                        url=url,
                        source="indeed",
                        posted_at=_clean(item.get("formattedRelativeTime")),
                        description_snippet=snippet,
                    )
                )
        except Exception as exc:
            logger.warning("Failed parsing Indeed mosaic data: %s", exc)

    # Fallback regex if mosaic JSON wasn't matched
    if not out:
        title_matches = list(re.finditer(r'<h2[^>]*class="[^"]*jobTitle[^"]*"[^>]*>.*?<span[^>]*>(.*?)</span>', html, re.S))
        for idx, tm in enumerate(title_matches):
            raw_title = _clean(re.sub(r'<[^>]+>', '', tm.group(1)))
            if raw_title:
                out.append(
                    JobListing(
                        id=f"indeed_regex_{idx}",
                        title=raw_title,
                        company="Indeed Employer",
                        location="Remote",
                        url="https://www.indeed.com",
                        source="indeed",
                        posted_at=None,
                        description_snippet="Software engineering position on Indeed",
                    )
                )

    return out


class IndeedScraper(BaseScraper):
    source_name = "indeed"

    def __init__(
        self,
        query: str = "software engineer",
        location: str = "Remote",
        max_pages: int = 1,
        proxy: Optional[str] = None,
    ):
        self.query = query
        self.location = location
        self.max_pages = max_pages
        self.proxy = proxy

    async def fetch(self, _session=None) -> list[JobListing]:
        jobs: list[JobListing] = []

        async with stealth_session(impersonate="chrome120", proxy=self.proxy) as client:
            for page_idx in range(self.max_pages):
                start = page_idx * 10
                url = "https://www.indeed.com/jobs"
                params = {
                    "q": self.query,
                    "l": self.location,
                    "start": start,
                }
                try:
                    resp = await client.get(url, params=params, timeout=20)
                    if resp.status_code == 200:
                        parsed = _parse_indeed_mosaic(resp.text)
                        jobs.extend(parsed)
                except Exception as exc:
                    logger.warning("Indeed curl_cffi error: %s", exc)

        return jobs
