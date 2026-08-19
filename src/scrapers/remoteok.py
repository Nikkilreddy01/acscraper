from __future__ import annotations

import time
from typing import List, Optional

from curl_cffi.requests import AsyncSession

from ._stealth import stealth_session, STEALTH_HEADERS, _jittered_sleep
from .base import BaseScraper, JobListing, timeframe_to_seconds


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

    async def fetch(
        self,
        _session=None,
        timeframe: str = "all",
        query: str | None = None,
        location: str | None = None,
    ) -> List[JobListing]:
        jobs: List[JobListing] = []
        sec_cutoff = timeframe_to_seconds(timeframe)
        min_epoch = (time.time() - sec_cutoff) if sec_cutoff else None
        q_lower = query.lower().strip() if query else None

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

                epoch = item.get("epoch")
                if min_epoch and epoch and epoch < min_epoch:
                    continue

                pos = (item.get("position") or "").strip()
                comp = (item.get("company") or "").strip()
                loc = item.get("location") or ""
                tags = item.get("tags") or []

                if q_lower:
                    searchable = f"{pos} {comp} {loc} {' '.join(tags)}".lower()
                    if q_lower not in searchable:
                        continue

                job = JobListing(
                    id=str(item.get("id", "")) or str(abs(hash(item.get("url", "")))),
                    title=pos,
                    company=comp or None,
                    location=loc or None,
                    url=item.get("url") or self.url,
                    source=self.source_name,
                    posted_at=self._iso(epoch),
                    description_snippet=tags[0] if tags else None,
                )
                jobs.append(job)

        return jobs
