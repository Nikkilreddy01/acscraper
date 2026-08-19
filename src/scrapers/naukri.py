from __future__ import annotations

import asyncio
import json
import logging
import re
from typing import Optional

from curl_cffi.requests import AsyncSession

from ._stealth import stealth_session, STEALTH_HEADERS, _jittered_sleep, launch_browser
from .base import BaseScraper, JobListing

logger = logging.getLogger(__name__)

UA = STEALTH_HEADERS["User-Agent"]

RSS_URL = "https://www.naukri.com/xmlfeedjobsfeed"
SEARCH_PAGE = "https://www.naukri.com/jobs-by-location"
SEARCH_API = "https://www.naukri.com/jobapi/v3/search"

JOB_URL_RE = re.compile(r"^https?://www\.naukri\.com/job-listings[^\"']*")
LOGIN_URL_RE = re.compile(r"/login|/login\.do|/userLogin", re.I)


def _clean(s: Optional[str]) -> Optional[str]:
    if not s:
        return None
    return " ".join(s.split())


def _epoch_to_iso(epoch: Optional[int]) -> Optional[str]:
    if not epoch:
        return None
    try:
        import time
        return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(epoch))
    except Exception:
        return str(epoch)


def _sanitize_url(url: Optional[str], job_id: str) -> str:
    if url and JOB_URL_RE.match(url):
        return url
    if url and url.startswith("http"):
        return url
    return f"https://www.naukri.com/jobs-by-location#{job_id}"


def _parse_job_details(
    job: dict,
    source: str,
    start_idx: int,
) -> Optional[JobListing]:
    """Parse one `jobDetails` item from Naukri's jobapi/v3/search response."""
    title = _clean(job.get("title"))
    if not title:
        return None

    job_id = str(job.get("jobId", start_idx))
    company = _clean(job.get("companyName"))
    location = None

    # salaryDetail may have min/max/currency
    salary_detail = job.get("salaryDetail") or {}
    salary_parts = []
    if salary_detail.get("minimumSalary") or salary_detail.get("maximumSalary"):
        mn = salary_detail.get("minimumSalary", "")
        mx = salary_detail.get("maximumSalary", "")
        cur = salary_detail.get("currency", "")
        if cur:
            salary_parts.append(f"{cur} ")
        if mn and mx:
            salary_parts.append(f"{mn}-{mx} LPA")
        elif mn:
            salary_parts.append(f"{mn}+ LPA")
        elif mx:
            salary_parts.append(f"upto {mx} LPA")
    salary_str = "".join(salary_parts) if salary_parts else None

    # Build display title: role + salary
    display_title = f"{title} ({salary_str})" if salary_str else title

    # jdURL is the canonical job posting URL
    jd_url = job.get("jdURL", "")
    url = _sanitize_url(jd_url, job_id)

    # Placeholders array often contains ["location", "salary", "experience"]
    placeholders = job.get("placeholders") or []
    exp_text = _clean(job.get("experienceText")) or (
        placeholders[1] if len(placeholders) > 1 else None
    )

    # Description snippet
    snippet = _clean(job.get("jobDescription"))
    if exp_text:
        snippet = f"{exp_text} | {snippet}" if snippet else exp_text

    posted_at = _epoch_to_iso(job.get("createdDate"))
    footer_label = _clean(job.get("footerPlaceholderLabel"))
    if footer_label and not posted_at:
        posted_at = footer_label

    return JobListing(
        id=f"naukri_{job_id}",
        title=display_title,
        company=company,
        location=location,
        url=url,
        source=source,
        posted_at=posted_at,
        description_snippet=snippet,
    )


# ------------------------------------------------------------------
# curl_cffi path (kept for completeness — Naukri's geo page is an
# empty Next.js shell, so this will normally return 0 jobs)
# ------------------------------------------------------------------

async def _fetch_rss(_stealth_kwargs=None) -> list[JobListing]:
    """Deprecated: RSS endpoint returns 404 from non-Indian IPs. Kept as fallback."""
    jobs: list[JobListing] = []
    try:
        async with stealth_session(impersonate="chrome145", proxy=None) as client:
            resp = await client.get(
                RSS_URL,
                headers={**STEALTH_HEADERS, "Accept": "application/rss+xml,text/xml,*/*"},
                timeout=30,
            )
            if resp.status_code != 200:
                logger.info("Naukri RSS status %d", resp.status_code)
                return jobs
            xml = resp.text
            items = re.split(r"<item>", xml)
            for idx, block in enumerate(items[1:]):
                title_m = re.search(r"<title>(.*?)</title>", block, re.S)
                link_m = re.search(r"<link>(.*?)</link>", block, re.S)
                pub_m = re.search(r"<pubDate>(.*?)</pubDate>", block, re.S)
                title = _clean(title_m.group(1) if title_m else None)
                if not title:
                    continue
                jobs.append(
                    JobListing(
                        id=f"naukri_rss_{idx}",
                        title=title,
                        company=None,
                        location=None,
                        url=link_m.group(1).strip() if link_m else RSS_URL,
                        source="naukri",
                        posted_at=pub_m.group(1).strip() if pub_m else None,
                        description_snippet=None,
                    )
                )
    except Exception as exc:
        logger.info("Naukri RSS fetch error: %s", exc)
    return jobs


# ------------------------------------------------------------------
# Playwright primary (browser-rendered)
# ------------------------------------------------------------------

async def _playwright_fetch_naukri(
    query: str,
    location: str = "Remote",
    max_pages: int = 1,
    timeout: int = 45,
    executable_path: Optional[str] = None,
) -> list[JobListing]:
    """
    Load Naukri search in a real browser, capture the jobapi/v3/search XHR,
    and parse jobs directly from the JSON response.
    """
    browser = None
    try:
        from playwright.async_api import async_playwright  # noqa: E402
    except ImportError as exc:
        raise RuntimeError(
            "playwright is required for Naukri browser fallback: pip install playwright"
        ) from exc

    try:
        browser = await launch_browser(executable_path=executable_path)
        context = await browser.new_context(user_agent=UA, locale="en-US")
        page = await context.new_page()

        # Intercept the job search API response
        search_api_body: Optional[str] = None

        async def _on_response(resp):
            nonlocal search_api_body
            if "jobapi/v3/search" in resp.url and resp.status == 200:
                try:
                    search_api_body = await resp.text()
                except Exception:
                    pass

        page.on("response", _on_response)

        # Navigate — Naukri geo-routes non-Indian IPs to /jobs-in-india
        search_url = (
            f"{SEARCH_PAGE}?q={query.replace(' ', '+')}&l={location.replace(' ', '+')}"
        )
        await page.goto(search_url, wait_until="domcontentloaded", timeout=timeout * 1000)
        # Give the JS XHR time to fire
        try:
            await page.wait_for_load_state("networkidle", timeout=10000)
        except Exception:
            await asyncio.sleep(4)

        jobs: list[JobListing] = []

        # --- Path 1: XHR response ---
        if search_api_body:
            try:
                data = json.loads(search_api_body)
                job_details = data.get("jobDetails") or []
                logger.info(
                    "Naukri: got %d jobs from jobapi/v3/search XHR",
                    len(job_details),
                )
                for idx, jd in enumerate(job_details):
                    job = _parse_job_details(jd, "naukri", idx)
                    if job:
                        jobs.append(job)
            except Exception as exc:
                logger.warning("Naukri API JSON parse error: %s", exc)

        # --- Path 2: Next.js preloadState fallback (no XHR) ---
        if not jobs:
            logger.info("Naukri: XHR not captured, trying preloadState DOM")
            preload_state = await page.evaluate(
                """
                () => {
                    const scripts = Array.from(document.querySelectorAll('script'));
                    for (const s of scripts) {
                        const t = s.textContent;
                        // Look for the preloadState JSON embedded in __next_f
                        const m = t.match(/"preloadState":\\{(.+)\\}/s);
                        if (m) return '{' + m[1] + '}';
                    }
                    return null;
                }
                """
            )
            if preload_state:
                try:
                    wrapped = json.loads("{" + preload_state + "}")
                    srp = (wrapped or {}).get("srpState", {})
                    search_resp = srp.get("searchResp", {})
                    job_details = search_resp.get("jobDetails") or []
                    logger.info(
                        "Naukri: got %d jobs from preloadState",
                        len(job_details),
                    )
                    for idx, jd in enumerate(job_details):
                        job = _parse_job_details(jd, "naukri", idx)
                        if job:
                            jobs.append(job)
                except Exception as exc:
                    logger.warning("Naukri preloadState parse error: %s", exc)

        await context.close()
        return jobs

    except Exception as exc:
        logger.warning("Naukri Playwright fetch failed: %s", exc)
        return []
    finally:
        # Browser is shared singleton — do not close here
        pass


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

    async def fetch(self, _session=None) -> list[JobListing]:
        # Strategy 1: curl_cffi (rarely succeeds from non-Indian IP due to geo-shell)
        try:
            curl_jobs = await _fetch_rss()
            if curl_jobs:
                return curl_jobs
        except Exception as exc:
            logger.info("Naukri curl_cffi path skipped: %s", exc)

        # Strategy 2: Playwright browser (primary — captures real job data)
        try:
            browser_jobs = await _playwright_fetch_naukri(
                query=self.keywords,
                location=self.location,
                max_pages=self.max_pages,
            )
            return browser_jobs
        except Exception as exc:
            logger.warning("Naukri Playwright path failed: %s", exc)
            return []