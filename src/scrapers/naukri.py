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


def _parse_job_details(
    job: dict,
    source: str,
    start_idx: int,
) -> Optional[JobListing]:
    """Parse one `jobDetails` item from Naukri's jobapi response."""
    title = _clean(job.get("title"))
    if not title:
        return None

    job_id = str(job.get("jobId", start_idx))
    company = _clean(job.get("companyName"))
    
    # Placeholders array contains ["experience", "salary", "location"]
    placeholders = job.get("placeholders") or []
    location = _clean(job.get("location")) or (placeholders[2] if len(placeholders) > 2 else "India")
    exp_text = _clean(job.get("experienceText")) or (placeholders[0] if len(placeholders) > 0 else "")

    jd_url = job.get("jdURL", "")
    if jd_url and not jd_url.startswith("http"):
        jd_url = f"https://www.naukri.com{jd_url}"
    url = jd_url or f"https://www.naukri.com/job-listings-{job_id}"

    snippet = _clean(job.get("jobDescription")) or exp_text
    posted_at = _clean(job.get("footerPlaceholderLabel")) or "Recent"

    return JobListing(
        id=f"naukri_{job_id}",
        title=title,
        company=company,
        location=location,
        url=url,
        source=source,
        posted_at=posted_at,
        description_snippet=snippet,
    )


async def _playwright_fetch_naukri(
    query: str = "software engineer",
    location: str = "Remote",
    timeout: int = 30,
    executable_path: Optional[str] = None,
) -> list[JobListing]:
    """Render Naukri in headless browser, intercept search API and parse job cards."""
    jobs: list[JobListing] = []
    browser = None
    context = None
    try:
        browser = await launch_browser(executable_path=executable_path)
        context = await browser.new_context(
            user_agent=UA,
            viewport={"width": 1280, "height": 800},
            locale="en-US",
        )
        page = await context.new_page()

        search_api_payloads = []

        async def _on_response(resp):
            if ("jobapi" in resp.url or "search" in resp.url) and resp.status == 200:
                try:
                    text = await resp.text()
                    if "jobDetails" in text:
                        search_api_payloads.append(text)
                except Exception:
                    pass

        page.on("response", _on_response)

        slug = query.lower().strip().replace(" ", "-")
        target_url = f"https://www.naukri.com/{slug}-jobs"

        await page.goto(target_url, wait_until="domcontentloaded", timeout=timeout * 1000)
        await asyncio.sleep(3)

        # 1. Parse from intercepted XHR
        for payload in search_api_payloads:
            try:
                data = json.loads(payload)
                jds = data.get("jobDetails") or []
                for idx, jd in enumerate(jds):
                    item = _parse_job_details(jd, "naukri", idx)
                    if item:
                        jobs.append(item)
            except Exception as exc:
                logger.debug("Failed parsing Naukri XHR: %s", exc)

        # 2. Parse from DOM if XHR wasn't intercepted
        if not jobs:
            cards = await page.locator(".srp-jobtuple-wrapper, .cust-job-tuple, article.jobTuple").all()
            for idx, card in enumerate(cards):
                try:
                    title_el = card.locator("a.title").first
                    title = _clean(await title_el.inner_text())
                    url = await title_el.get_attribute("href") or target_url
                    if url and not url.startswith("http"):
                        url = f"https://www.naukri.com{url}"

                    company_el = card.locator("a.comp-name, .company-name").first
                    company = _clean(await company_el.inner_text()) if await company_el.count() > 0 else "Tech Company"

                    loc_el = card.locator(".locWdth, .loc-wrap, .location").first
                    location_text = _clean(await loc_el.inner_text()) if await loc_el.count() > 0 else location

                    desc_el = card.locator(".job-desc, .job-description").first
                    desc = _clean(await desc_el.inner_text()) if await desc_el.count() > 0 else "Software Engineer opening on Naukri"

                    if title:
                        jobs.append(
                            JobListing(
                                id=f"naukri_dom_{idx}",
                                title=title,
                                company=company,
                                location=location_text,
                                url=url,
                                source="naukri",
                                posted_at="Recent",
                                description_snippet=desc,
                            )
                        )
                except Exception as exc:
                    logger.debug("DOM parse error: %s", exc)

        await context.close()
        return jobs
    except Exception as exc:
        logger.warning("Naukri Playwright error: %s", exc)
        if context:
            try:
                await context.close()
            except Exception:
                pass
        return []


class NaukriScraper(BaseScraper):
    source_name = "naukri"

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
        self.proxy = proxy

    async def fetch(
        self,
        _session=None,
        timeframe: str = "all",
        query: str | None = None,
        location: str | None = None,
    ) -> List[JobListing]:
        try:
            return await _playwright_fetch_naukri(
                query=query or self.keywords,
                location=location or self.location,
            )
        except Exception as exc:
            logger.warning("Naukri scraper failed: %s", exc)
            return []
