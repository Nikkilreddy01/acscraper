from fastapi import FastAPI, Request, Query
from fastapi.responses import HTMLResponse
from fastapi.responses import JSONResponse
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel
from typing import List

from src.scrapers.remoteok import RemoteOKScraper
from src.scrapers.linkedin import LinkedInScraper
from src.scrapers.indeed import IndeedScraper
from src.scrapers.naukri import NaukriScraper

app = FastAPI(title="AcScraper", version="0.3.0")

templates = Jinja2Templates(directory="templates")

SCRAPERS = {
    "remoteok": RemoteOKScraper(),
    "linkedin": LinkedInScraper(),
    "indeed": IndeedScraper(),
    "naukri": NaukriScraper(),
}


class ScrapeResponse(BaseModel):
    source: str
    count: int
    jobs: List[dict]


@app.get("/", response_class=HTMLResponse)
async def home(request: Request):
    return templates.TemplateResponse("index.html", {"request": request})


@app.get("/api/scrape", response_model=ScrapeResponse)
async def scrape(source: str = Query(default="remoteok")):
    scraper = SCRAPERS.get(source)
    if not scraper:
        return JSONResponse(
            status_code=400,
            content={"error": f"Unknown source '{source}'. Available: {sorted(SCRAPERS)}"},
        )
    try:
        jobs = await scraper.fetch(None)
    except Exception as exc:
        return JSONResponse(
            status_code=503,
            content={"error": f"{source} unavailable: {exc}"},
        )
    return ScrapeResponse(
        source=source,
        count=len(jobs),
        jobs=[j.model_dump() for j in jobs],
    )


@app.get("/health")
async def health():
    return {"status": "ok"}