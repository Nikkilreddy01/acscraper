from __future__ import annotations

import asyncio
import time
import logging
from fastapi import FastAPI, Request, Query
from fastapi.responses import HTMLResponse
from fastapi.responses import JSONResponse
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel
from typing import List, Dict, Any, Optional

from src.scrapers.remoteok import RemoteOKScraper
from src.scrapers.linkedin import LinkedInScraper
from src.scrapers.indeed import IndeedScraper
from src.scrapers.naukri import NaukriScraper
from src.scrapers.hn_algo import HNAlgoliaScraper
from src.scrapers.ziprecruiter import ZipRecruiterScraper
from src.scrapers.glassdoor import GlassdoorScraper

logger = logging.getLogger(__name__)

app = FastAPI(title="AcScraper", version="0.4.0")

templates = Jinja2Templates(directory="templates")

SCRAPERS = {
    "remoteok": RemoteOKScraper(),
    "linkedin": LinkedInScraper(),
    "indeed": IndeedScraper(),
    "hackernews": HNAlgoliaScraper(),
    "ziprecruiter": ZipRecruiterScraper(),
    "glassdoor": GlassdoorScraper(),
    "naukri": NaukriScraper(),
}

# In-memory cache: {cache_key: {"jobs": [...], "timestamp": float}}
CACHE: Dict[str, Dict[str, Any]] = {}
CACHE_TTL = 300  # 5 minutes for success
EMPTY_CACHE_TTL = 60  # 1 minute for empty/failed attempts


class ScrapeResponse(BaseModel):
    source: str
    count: int
    jobs: List[dict]


async def fetch_single_scraper(
    name: str,
    scraper,
    timeframe: str = "all",
    query: Optional[str] = None,
    location: Optional[str] = None,
    timeout_seconds: float = 6.0,
    force: bool = False,
) -> List[dict]:
    """Fetch jobs from a scraper with cache and strict per-scraper timeout."""
    cache_key = f"{name}_{timeframe}_{query or ''}_{location or ''}"
    now = time.time()
    cached = CACHE.get(cache_key)
    if not force and cached:
        ttl = CACHE_TTL if cached["jobs"] else EMPTY_CACHE_TTL
        if (now - cached["timestamp"]) < ttl:
            return cached["jobs"]

    try:
        raw_jobs = await asyncio.wait_for(
            scraper.fetch(
                None,
                timeframe=timeframe,
                query=query,
                location=location,
            ),
            timeout=timeout_seconds,
        )
        dumped = [j.model_dump() for j in raw_jobs] if raw_jobs else []
        CACHE[cache_key] = {"jobs": dumped, "timestamp": now}
        return dumped
    except Exception as exc:
        logger.warning("Scraper %s error or timeout: %s", name, exc)
        if cached and cached.get("jobs"):
            return cached["jobs"]
        CACHE[cache_key] = {"jobs": [], "timestamp": now}
        return []


@app.on_event("startup")
async def preload_cache():
    """Warm up cache on server start in background."""
    asyncio.create_task(warm_cache())


async def warm_cache():
    tasks = [
        fetch_single_scraper(name, scraper, timeframe="all", timeout_seconds=8.0)
        for name, scraper in SCRAPERS.items()
    ]
    await asyncio.gather(*tasks, return_exceptions=True)


@app.get("/", response_class=HTMLResponse)
async def home(request: Request):
    return templates.TemplateResponse(request=request, name="index.html")


@app.get("/api/scrape", response_model=ScrapeResponse)
async def scrape(
    source: str = Query(default="all"),
    timeframe: str = Query(default="all", description="Timeframe filter: all, 24h, 7d, 1m, 3m, 6m, 1y"),
    query: Optional[str] = Query(default=None),
    location: Optional[str] = Query(default=None),
    force: bool = Query(default=False),
):
    if source == "all":
        tasks = [
            fetch_single_scraper(
                name,
                scraper,
                timeframe=timeframe,
                query=query,
                location=location,
                timeout_seconds=5.0,
                force=force,
            )
            for name, scraper in SCRAPERS.items()
        ]
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

    jobs = await fetch_single_scraper(
        source,
        scraper,
        timeframe=timeframe,
        query=query,
        location=location,
        timeout_seconds=8.0,
        force=force,
    )
    return ScrapeResponse(
        source=source,
        count=len(jobs),
        jobs=jobs,
    )


@app.get("/health")
async def health():
    return {"status": "ok"}
