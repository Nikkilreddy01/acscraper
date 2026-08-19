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
CACHE_TTL = 300  # 5 minutes for success
EMPTY_CACHE_TTL = 120  # 2 minutes for empty/failed attempts


class ScrapeResponse(BaseModel):
    source: str
    count: int
    jobs: List[dict]


async def fetch_single_scraper(name: str, scraper, timeout_seconds: float = 4.0) -> List[dict]:
    """Fetch jobs from a scraper with cache and strict per-scraper timeout."""
    now = time.time()
    cached = CACHE.get(name)
    if cached:
        ttl = CACHE_TTL if cached["jobs"] else EMPTY_CACHE_TTL
        if (now - cached["timestamp"]) < ttl:
            return cached["jobs"]

    try:
        raw_jobs = await asyncio.wait_for(scraper.fetch(None), timeout=timeout_seconds)
        dumped = [j.model_dump() for j in raw_jobs] if raw_jobs else []
        CACHE[name] = {"jobs": dumped, "timestamp": now}
        return dumped
    except Exception as exc:
        logger.warning("Scraper %s error or timeout: %s", name, exc)
        # Cache empty response for EMPTY_CACHE_TTL to prevent repeatedly stalling clients
        if cached and cached.get("jobs"):
            return cached["jobs"]
        CACHE[name] = {"jobs": [], "timestamp": now}
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