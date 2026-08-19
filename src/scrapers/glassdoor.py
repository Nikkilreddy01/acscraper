from __future__ import annotations

import asyncio
import json
import logging
import re
from typing import Optional, List

from curl_cffi.requests import AsyncSession

from ._stealth import stealth_session, STEALTH_HEADERS, _jittered_sleep, launch_browser
from .base import BaseScraper, JobListing, timeframe_to_days

logger = logging.getLogger(__name__)


def _clean(s: Optional[str]) -> Optional[str]:
    if not s:
        return None
    return " ".join(s.split())


async def _playwright_fetch_glassdoor(
    query: str = "software engineer",
    location: str = "Remote",
    fromage: Optional[int] = None,
    timeout: int = 30,
) -> List[JobListing]:
    """Render Glassdoor in headless browser and extract job cards."""
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

        url = f"https://www.glassdoor.com/Job/jobs.htm?sc.keyword={query.replace(' ', '+')}&locT=C&locId=11047"
        if fromage:
            url += f"&fromAge={fromage}"

        await page.goto(url, wait_until="domcontentloaded", timeout=timeout * 1000)
        await asyncio.sleep(3)

        cards = await page.locator("li[data-test='jobListing'], .JobCard_jobCardWrapper__1PTr9, [data-jobid]").all()
        for idx, card in enumerate(cards):
            try:
                title_el = card.locator("a.JobCard_jobTitle___P9L7, a[data-test='job-title']").first
                title = _clean(await title_el.inner_text()) if await title_el.count() > 0 else ""
                if not title:
                    continue

                url_val = await title_el.get_attribute("href") or url
                if url_val and not url_val.startswith("http"):
                    url_val = f"https://www.glassdoor.com{url_val}"

                comp_el = card.locator(".EmployerProfile_compactEmployerName__LE242, [data-test='employer-name']").first
                comp = _clean(await comp_el.inner_text()) if await comp_el.count() > 0 else "Glassdoor Employer"

                loc_el = card.locator("[data-test='job-location'], .JobCard_location__1G1g_").first
                loc = _clean(await loc_el.inner_text()) if await loc_el.count() > 0 else location

                snippet_el = card.locator(".JobCard_jobDescriptionSnippet__qNmsg").first
                snippet = _clean(await snippet_el.inner_text()) if await snippet_el.count() > 0 else "Glassdoor job listing"

                jobs.append(
                    JobListing(
                        id=f"gd_{idx}",
                        title=title,
                        company=comp,
                        location=loc,
                        url=url_val,
                        source="glassdoor",
                        posted_at="Recent",
                        description_snippet=snippet,
                    )
                )
            except Exception:
                pass

        await context.close()
        return jobs
    except Exception as exc:
        logger.warning("Glassdoor Playwright error: %s", exc)
        if context:
            try:
                await context.close()
            except Exception:
                pass
        return []


class GlassdoorScraper(BaseScraper):
    source_name = "glassdoor"

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
        q = query or self.query
        loc = location or self.location
        fromage = timeframe_to_days(timeframe)

        # Fallback to Playwright
        try:
            return await _playwright_fetch_glassdoor(q, loc, fromage=fromage)
        except Exception as exc:
            logger.warning("Glassdoor scraper failed: %s", exc)
            return []
