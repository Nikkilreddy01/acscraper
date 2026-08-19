import asyncio
import time
import logging
from fastapi import FastAPI, Request, Query
from fastapi.responses import HTMLResponse
from fastapi.responses import JSONResponse
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel
from typing import List, Dict, Any

from src.scrapers.remoteok import RemoteOKScraper
from src.scrapers.linkedin import LinkedInScraper
from src.scrapers.indeed import IndeedScraper
from src.scrapers.naukri import NaukriScraper

logger = logging.getLogger(__name__)

app = FastAPI(title="AcScraper", version="0.3.0")

templates = Jinja2Templates(directory="templates")

SCRAPERS = {
    "remoteok": RemoteOKScraper(),
    "linkedin": LinkedInScraper(),
    "indeed": IndeedScraper(),
    "naukri": NaukriScraper(),
}

# In-memory cache: {source_name: {"jobs": [...], "timestamp": float}}
CACHE: Dict[str, Dict[str, Any]] = {}
CACHE_TTL = 300  # 5 minutes


class ScrapeResponse(BaseModel):
    source: str
    count: int
    jobs: List[dict]


async def fetch_single_scraper(name: str, scraper, timeout_seconds: float = 6.0) -> List[dict]:
    """Fetch jobs from a scraper with cache and strict per-scraper timeout."""
    now = time.time()
    cached = CACHE.get(name)
    if cached and (now - cached["timestamp"] < CACHE_TTL) and cached["jobs"]:
        return cached["jobs"]

    try:
        raw_jobs = await asyncio.wait_for(scraper.fetch(None), timeout=timeout_seconds)
        dumped = [j.model_dump() for j in raw_jobs] if raw_jobs else []
        if dumped:
            CACHE[name] = {"jobs": dumped, "timestamp": now}
            return dumped
        elif cached and cached["jobs"]:
            # Fall back to existing cached items if available
            return cached["jobs"]
        return []
    except Exception as exc:
        logger.warning("Scraper %s error or timeout: %s", name, exc)
        if cached and cached["jobs"]:
            return cached["jobs"]
        return []


@app.on_event("startup")
async def preload_cache():
    """Warm up cache on server start in background."""
    asyncio.create_task(warm_cache())


async def warm_cache():
    tasks = [fetch_single_scraper(name, scraper, timeout_seconds=8.0) for name, scraper in SCRAPERS.items()]
    await asyncio.gather(*tasks, return_exceptions=True)


@app.get("/", response_class=HTMLResponse)
async def home(request: Request):
    return templates.TemplateResponse(request=request, name="index.html")


@app.get("/api/scrape", response_model=ScrapeResponse)
async def scrape(source: str = Query(default="all")):
    if source == "all":
        tasks = [fetch_single_scraper(name, scraper, timeout_seconds=5.0) for name, scraper in SCRAPERS.items()]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        all_jobs = []
        for res in results:
            if isinstance(res, list):
                all_jobs.extend(res)

        return ScrapeResponse(
            source="all",
            count=len(all_jobs),
            jobs=all_jobs,
        )

    scraper = SCRAPERS.get(source)
    if not scraper:
        return JSONResponse(
            status_code=400,
            content={"error": f"Unknown source '{source}'. Available: {sorted(SCRAPERS)} + ['all']"},
        )

    jobs = await fetch_single_scraper(source, scraper, timeout_seconds=8.0)
    return ScrapeResponse(
        source=source,
        count=len(jobs),
        jobs=jobs,
    )


@app.get("/health")
async def health():
    return {"status": "ok"}