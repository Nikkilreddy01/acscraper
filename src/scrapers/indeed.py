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

UA = STEALTH_HEADERS["User-Agent"]


def _clean(s: Optional[str]) -> Optional[str]:
    if not s:
        return None
    return " ".join(s.split())


def _parse_indeed_mosaic(html: str) -> list[JobListing]:
    """Parse Indeed's embedded JSON mosaic-provider-jobcards data."""
    out: list[JobListing] = []

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

    return out


async def _playwright_fetch_indeed(
    query: str = "software engineer",
    location: str = "Remote",
    fromage: Optional[int] = None,
    timeout: int = 30,
) -> list[JobListing]:
    """Fallback: render Indeed in headless browser and scrape job cards."""
    jobs: list[JobListing] = []
    browser = None
    context = None
    try:
        browser = await launch_browser()
        context = await browser.new_context(
            user_agent=UA,
            viewport={"width": 1280, "height": 800},
            locale="en-US",
        )
        page = await context.new_page()

        url = f"https://www.indeed.com/jobs?q={query.replace(' ', '+')}&l={location.replace(' ', '+')}"
        if fromage:
            url += f"&fromage={fromage}"

        await page.goto(url, wait_until="domcontentloaded", timeout=timeout * 1000)
        await asyncio.sleep(3)

        html = await page.content()
        parsed = _parse_indeed_mosaic(html)
        if parsed:
            jobs = parsed
        else:
            cards = await page.locator("[data-jk], .slider_container, .job_seen_beacon").all()
            for idx, card in enumerate(cards):
                try:
                    title_el = card.locator("h2.jobTitle, a.jcs-JobTitle").first
                    title = _clean(await title_el.inner_text()) if await title_el.count() > 0 else ""
                    if not title:
                        continue
                    company_el = card.locator("[data-testid='company-name'], .companyName").first
                    company = _clean(await company_el.inner_text()) if await company_el.count() > 0 else "Employer"
                    loc_el = card.locator("[data-testid='text-location'], .companyLocation").first
                    loc = _clean(await loc_el.inner_text()) if await loc_el.count() > 0 else location
                    jk = await card.get_attribute("data-jk") or str(idx)

                    jobs.append(
                        JobListing(
                            id=f"indeed_pw_{jk}",
                            title=title,
                            company=company,
                            location=loc,
                            url=f"https://www.indeed.com/viewjob?jk={jk}",
                            source="indeed",
                            posted_at="Recent",
                            description_snippet="Software position on Indeed",
                        )
                    )
                except Exception:
                    pass

        await context.close()
        return jobs
    except Exception as exc:
        logger.warning("Indeed Playwright error: %s", exc)
        if context:
            try:
                await context.close()
            except Exception:
                pass
        return []


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

    async def fetch(
        self,
        _session=None,
        timeframe: str = "all",
        query: str | None = None,
        location: str | None = None,
    ) -> List[JobListing]:
        jobs: list[JobListing] = []
        q = query or self.query
        loc = location or self.location
        fromage = timeframe_to_days(timeframe)

        # Strategy 1: Fast TLS impersonation
        async with stealth_session(impersonate="chrome120", proxy=self.proxy) as client:
            for page_idx in range(self.max_pages):
                start = page_idx * 10
                url = "https://www.indeed.com/jobs"
                params = {
                    "q": q,
                    "l": loc,
                    "start": start,
                }
                if fromage:
                    params["fromage"] = fromage

                try:
                    resp = await client.get(url, params=params, timeout=15)
                    if resp.status_code == 200:
                        parsed = _parse_indeed_mosaic(resp.text)
                        jobs.extend(parsed)
                except Exception as exc:
                    logger.warning("Indeed curl_cffi error: %s", exc)

        if jobs:
            return jobs

        # Strategy 2: Headless Playwright Fallback
        try:
            pw_jobs = await _playwright_fetch_indeed(q, loc, fromage=fromage)
            return pw_jobs
        except Exception as exc:
            logger.warning("Indeed Playwright fallback failed: %s", exc)
            return []
