from __future__ import annotations

import asyncio
import logging
import re
from typing import Optional

from curl_cffi.requests import AsyncSession

from ._stealth import stealth_session, STEALTH_HEADERS, _jittered_sleep, close_browser, launch_browser
from .base import BaseScraper, JobListing

logger = logging.getLogger(__name__)

UA = STEALTH_HEADERS["User-Agent"]

INDEED_TITLE_RE = re.compile(
    r'<h2[^>]*class="jobTitle[^"]*"[^>]*>.*?<a[^>]+title="([^"]+)"', re.S
)
INDEED_URL_RE = re.compile(
    r'<h2[^>]*class="jobTitle[^"]*"[^>]*>.*?<a[^>]+href="(/jobs\?[^"]+)"', re.S
)
INDEED_COMPANY_RE = re.compile(
    r'<span[^>]*class="[^"]*companyName[^"]*"[^>]*>(.*?)</span>', re.S
)
INDEED_LOCATION_RE = re.compile(
    r'<div[^>]*class="[^"]*companyLocation[^"]*"[^>]*>(.*?)</div>', re.S
)
INDEED_DESC_RE = re.compile(
    r'<div[^>]*class="[^"]*job-snippet[^"]*"[^>]*>(.*?)</div>', re.S
)
INDEED_SALARY_RE = re.compile(
    r'<div[^>]*class="[^"]*salary-snippet-container[^"]*"[^>]*>(.*?)</div>', re.S
)


def _clean(s: Optional[str]) -> Optional[str]:
    if not s:
        return None
    return " ".join(s.split())


def _parse_indeed_html(html: str, start: int = 0) -> list[JobListing]:
    out: list[JobListing] = []
    tiles = list(INDEED_TITLE_RE.finditer(html))

    for m_idx, m in enumerate(tiles):
        title = _clean(m.group(1))
        if not title:
            continue

        card_ctx = html[m.start() : m.start() + 4000]

        url_m = INDEED_URL_RE.search(card_ctx)
        relative_url = url_m.group(1) if url_m else ""
        full_url = (
            f"https://www.indeed.com{relative_url}" if relative_url else "https://www.indeed.com"
        )

        company_m = INDEED_COMPANY_RE.search(card_ctx)
        company = _clean(company_m.group(1) if company_m else None)

        loc_m = INDEED_LOCATION_RE.search(card_ctx)
        location = _clean(loc_m.group(1) if loc_m else None)

        salary_m = INDEED_SALARY_RE.search(card_ctx)
        salary = _clean(salary_m.group(1) if salary_m else None)

        desc_m = INDEED_DESC_RE.search(html, m.end())
        snippet = _clean(desc_m.group(1) if desc_m else None)

        parts = [p for p in [title, salary] if p]
        display_title = " | ".join(parts) if salary else title

        out.append(
            JobListing(
                id=f"indeed_{start + m_idx}",
                title=display_title,
                company=company,
                location=location,
                url=full_url,
                source="indeed",
                posted_at=None,
                description_snippet=snippet,
            )
        )
    return out


def _is_bot_block(response_text: str, status: int) -> bool:
    """Return True when the response is clearly a bot-blocking page."""
    if status in (403, 429):
        return True
    if status != 200:
        return True
    text_lower = response_text.lower()
    block_signals = [
        "cloudflare",
        "captcha",
        "access denied",
        "verify you are human",
        "are you a robot",
        "just a moment",
        "request blocked",
        "security check",
        "lightningcss",
    ]
    return any(sig in text_lower for sig in block_signals)


async def _playwright_fetch_indeed(
    query: str, location: str, timeout: int = 45
) -> list[JobListing]:
    """Fallback: render Indeed in a real Chromium browser and scrape job cards."""
    try:
        browser = await launch_browser()
    except Exception as exc:
        logger.warning("Playwright browser unavailable: %s", exc)
        return []

    context = None
    try:
        context = await browser.new_context(user_agent=UA, locale="en-US")
        page = await context.new_page()

        url = (
            f"https://www.indeed.com/jobs?"
            f"q={query.replace(' ', '+')}&l={location.replace(' ', '+')}"
        )

        await page.goto(url, wait_until="domcontentloaded", timeout=timeout * 1000)

        # Wait for job cards — Indeed marks each card with data-jk
        try:
            await page.wait_for_selector('[data-jk]', timeout=20000)
        except Exception:
            pass  # page may have loaded without the selector matching immediately

        await asyncio.sleep(2)

        # Extract jobs via Playwright locators (robust against DOM changes)
        # Each job card is a .slider_container div; text lines are:
        #   [0] title, [1] company, [2] location, [3+] benefits/snippet
        cards = await page.locator(".slider_container").all()
        jobs: list[JobListing] = []
        for card in cards:
            try:
                jk_el = card.locator("[data-jk]").first
                jk = await jk_el.get_attribute("data-jk") or ""

                href = await jk_el.get_attribute("href") or ""
                if href and not href.startswith("http"):
                    href = f"https://www.indeed.com{href}"

                full_text = (await card.inner_text()).strip()
                lines = [l.strip() for l in full_text.split("\n") if l.strip()]
                if not lines:
                    continue
                title = lines[0]
                company = lines[1] if len(lines) > 1 else ""
                location = ""
                if len(lines) > 2:
                    loc_line = lines[2]
                    if not loc_line.startswith(
                        ("Salary Search", "View all", "See popular", "View similar")
                    ):
                        location = loc_line

                snippet_parts: list[str] = []
                for line in lines[3:]:
                    if line.startswith(
                        ("Salary Search", "View all", "See popular", "View similar")
                    ):
                        break
                    snippet_parts.append(line)
                snippet = " | ".join(snippet_parts[:3]) if snippet_parts else ""

                if not title:
                    continue

                jobs.append(
                    JobListing(
                        id=f"indeed_{jk or len(jobs)}",
                        title=title,
                        company=company or None,
                        location=location or None,
                        url=href or "https://www.indeed.com",
                        source="indeed",
                        posted_at=None,
                        description_snippet=snippet or None,
                    )
                )
            except Exception as exc:
                logger.debug("Indeed card parse error: %s", exc)

        await context.close()
        return jobs

    except Exception as exc:
        logger.warning("Playwright indeed fallback failed: %s", exc)
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

    async def fetch(self, _session=None) -> list[JobListing]:
        curl_jobs: list[JobListing] = []

        # --- Strategy 1: curl_cffi (fast, no browser) ---
        async with stealth_session(impersonate="chrome145", proxy=self.proxy) as client:
            for page_idx in range(self.max_pages):
                start = page_idx * 10
                url = "https://www.indeed.com/jobs"
                params = {"q": self.query, "l": self.location, "start": start}
                try:
                    resp = await client.get(
                        url,
                        params=params,
                        headers={
                            "Referer": "https://www.indeed.com/",
                            **STEALTH_HEADERS,
                        },
                        timeout=30,
                    )
                    html = resp.text
                    if _is_bot_block(html, resp.status_code):
                        logger.info(
                            "Indeed curl_cffi blocked (status=%d); falling back to browser",
                            resp.status_code,
                        )
                        break  # fall through to Playwright
                    page_jobs = _parse_indeed_html(html, start=start)
                    curl_jobs.extend(page_jobs)
                except Exception as exc:
                    logger.warning("Indeed curl_cffi page %d error: %s", page_idx, exc)
                    break

        if curl_jobs:
            return curl_jobs

        # --- Strategy 2: Playwright browser fallback ---
        try:
            logger.info("Indeed: attempting Playwright fallback")
            browser_jobs = await _playwright_fetch_indeed(self.query, self.location)
            return browser_jobs
        except Exception as exc:
            logger.warning("Indeed Playwright fallback not available: %s", exc)
            return []

        return []