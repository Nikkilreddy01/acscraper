from __future__ import annotations

import asyncio
import json
import logging
import re
from typing import Optional, List
from datetime import datetime

from curl_cffi.requests import AsyncSession

from ._stealth import stealth_session, STEALTH_HEADERS, _jittered_sleep, launch_browser
from .base import BaseScraper, JobListing, timeframe_to_days

logger = logging.getLogger(__name__)

ZR_MOBILE_HEADERS = {
    "Host": "api.ziprecruiter.com",
    "accept": "*/*",
    "x-zr-zva-override": "100000000;vid:ZT1huzm_EQlDTVEc",
    "x-pushnotificationid": "0ff4983d38d7fc5b3370297f2bcffcf4b3321c418f5c22dd152a0264707602a0",
    "x-deviceid": "D77B3A92-E589-46A4-8A39-6EF6F1D86006",
    "user-agent": "Job Search/87.0 (iPhone; CPU iOS 16_6_1 like Mac OS X)",
    "authorization": "Basic YTBlZjMyZDYtN2I0Yy00MWVkLWEyODMtYTI1NDAzMzI0YTcyOg==",
    "accept-language": "en-US,en;q=0.9",
}


def _clean(s: Optional[str]) -> Optional[str]:
    if not s:
        return None
    return " ".join(s.split())


async def _playwright_fetch_ziprecruiter(
    query: str = "software engineer",
    location: str = "Remote",
    days: Optional[int] = None,
    timeout: int = 30,
) -> List[JobListing]:
    """Render ZipRecruiter in headless browser and extract job cards."""
    jobs: List[JobListing] = []
    browser = None
    context = None
    try:
        browser = await launch_browser()
        context = await browser.new_context(
            user_agent=STEALTH_HEADERS["User-Agent"],
            viewport={"width": 1280, "height": 800},
            locale="en-US",
        )
        page = await context.new_page()

        url = f"https://www.ziprecruiter.com/jobs-search?search={query.replace(' ', '+')}&location={location.replace(' ', '+')}"
        if days:
            url += f"&days={days}"

        await page.goto(url, wait_until="domcontentloaded", timeout=timeout * 1000)
        await asyncio.sleep(3)

        # Parse job cards
        cards = await page.locator("article.job_result, div.job_content, [data-job-id]").all()
        for idx, card in enumerate(cards):
            try:
                title_el = card.locator("h2, a.job_link, a.job_title").first
                title = _clean(await title_el.inner_text()) if await title_el.count() > 0 else ""
                if not title:
                    continue

                url_val = await title_el.get_attribute("href") or url
                if url_val and not url_val.startswith("http"):
                    url_val = f"https://www.ziprecruiter.com{url_val}"

                comp_el = card.locator("a.company_name, .company_name, [data-testid='company-name']").first
                comp = _clean(await comp_el.inner_text()) if await comp_el.count() > 0 else "ZipRecruiter Employer"

                loc_el = card.locator(".location, [data-testid='job-location']").first
                loc = _clean(await loc_el.inner_text()) if await loc_el.count() > 0 else location

                snippet_el = card.locator(".job_snippet, .snippet").first
                snippet = _clean(await snippet_el.inner_text()) if await snippet_el.count() > 0 else "ZipRecruiter job opening"

                jobs.append(
                    JobListing(
                        id=f"zr_{idx}",
                        title=title,
                        company=comp,
                        location=loc,
                        url=url_val,
                        source="ziprecruiter",
                        posted_at="Recent",
                        description_snippet=snippet,
                    )
                )
            except Exception:
                pass

        await context.close()
        return jobs
    except Exception as exc:
        logger.warning("ZipRecruiter Playwright error: %s", exc)
        if context:
            try:
                await context.close()
            except Exception:
                pass
        return []


class ZipRecruiterScraper(BaseScraper):
    source_name = "ziprecruiter"

    def __init__(
        self,
        query: str = "software engineer",
        location: str = "Remote",
        proxy: Optional[str] = None,
    ):
        self.query = query
        self.location = location
        self.proxy = proxy

    async def fetch(
        self,
        _session=None,
        timeframe: str = "all",
        query: str | None = None,
        location: str | None = None,
    ) -> List[JobListing]:
        jobs: List[JobListing] = []
        q = query or self.query
        loc = location or self.location
        days = timeframe_to_days(timeframe)

        # Strategy 1: Mobile API
        async with stealth_session(impersonate="chrome120", proxy=self.proxy) as client:
            try:
                params = {
                    "search": q,
                    "location": loc,
                    "page": 1,
                    "per_page": 20,
                }
                if days:
                    params["days"] = days

                resp = await client.get(
                    "https://api.ziprecruiter.com/jobs-app/jobs",
                    params=params,
                    headers=ZR_MOBILE_HEADERS,
                    timeout=15,
                )
                if resp.status_code == 200:
                    data = resp.json()
                    for item in data.get("jobs", []):
                        title = _clean(item.get("name"))
                        if not title:
                            continue
                        key = item.get("listing_key", "")
                        comp = _clean(item.get("hiring_company", {}).get("name"))
                        city = item.get("job_city", "")
                        state = item.get("job_state", "")
                        job_loc = f"{city}, {state}".strip(", ") or loc
                        job_url = f"https://www.ziprecruiter.com/jobs//j?lvk={key}" if key else "https://www.ziprecruiter.com"
                        snippet = _clean(item.get("job_description"))

                        jobs.append(
                            JobListing(
                                id=f"zr_{key or len(jobs)}",
                                title=title,
                                company=comp,
                                location=job_loc,
                                url=job_url,
                                source=self.source_name,
                                posted_at=_clean(item.get("posted_time")),
                                description_snippet=snippet,
                            )
                        )
            except Exception as exc:
                logger.debug("ZipRecruiter mobile API error: %s", exc)

        if jobs:
            return jobs

        # Strategy 2: Playwright fallback
        try:
            return await _playwright_fetch_ziprecruiter(q, loc, days=days)
        except Exception as exc:
            logger.warning("ZipRecruiter fallback failed: %s", exc)
            return []
