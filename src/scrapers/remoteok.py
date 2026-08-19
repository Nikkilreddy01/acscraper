from __future__ import annotations

import time
from typing import List

from curl_cffi.requests import AsyncSession

from ._stealth import stealth_session, STEALTH_HEADERS, _jittered_sleep
from .base import BaseScraper, JobListing


class RemoteOKScraper(BaseScraper):
    source_name: str = "remoteok"

    def __init__(self, url: str = "https://remoteok.com/api"):
        self.url = url

    @staticmethod
    def _iso(epoch: float | None) -> str | None:
        if not epoch:
            return None
        try:
            return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(epoch))
        except Exception:
            return str(epoch)

    async def fetch(self, _session=None) -> List[JobListing]:
        jobs: List[JobListing] = []
        async with stealth_session(impersonate="chrome145") as client:
            try:
                resp = await client.get(
                    self.url,
                    headers={**STEALTH_HEADERS, "Accept": "application/json,*/*"},
                    timeout=30,
                )
                resp.raise_for_status()
                data = resp.json()
                if not isinstance(data, list):
                    return jobs
            except Exception as exc:
                raise RuntimeError(f"RemoteOK fetch failed: {exc}") from exc

            for item in data:
                if not isinstance(item, dict) or not item.get("position"):
                    continue
                tags = item.get("tags") or []
                job = JobListing(
                    id=str(item.get("id", "")) or str(abs(hash(item.get("url", "")))),
                    title=(item.get("position") or "").strip(),
                    company=(item.get("company") or "").strip() or None,
                    location=item.get("location") or None,
                    url=item.get("url") or self.url,
                    source=self.source_name,
                    posted_at=self._iso(item.get("epoch")),
                    description_snippet=tags[0] if tags else None,
                )
                jobs.append(job)

        return jobs