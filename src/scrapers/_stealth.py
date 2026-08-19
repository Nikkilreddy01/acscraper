from __future__ import annotations

import time
import logging
from typing import Optional

try:
    from curl_cffi.requests import AsyncSession, BrowserTypeLiteral
except ImportError as e:
    raise ImportError("curl-cffi required for hostile sources: pip install curl-cffi") from e

logger = logging.getLogger(__name__)

CHROME_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_0) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/128.0.0.0 Safari/537.36"
)

STEALTH_HEADERS: dict[str, str] = {
    "User-Agent": CHROME_UA,
    "Accept": (
        "text/html,application/xhtml+xml,application/xml;"
        "q=0.9,image/avif,image/webp,*/*;q=0.8"
    ),
    "Accept-Language": "en-US,en;q=0.9",
    "Accept-Encoding": "gzip, deflate, br",
    "Connection": "keep-alive",
    "Upgrade-Insecure-Requests": "1",
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "none",
    "Sec-Fetch-User": "?1",
}


def _jittered_sleep(lo: float = 2.0, hi: float = 6.0) -> None:
    time.sleep(lo + (hi - lo) * __import__("random").random())


def stealth_session(
    impersonate: BrowserTypeLiteral = "chrome145",
    proxy: Optional[str] = None,
) -> AsyncSession:
    """Curled session that mimics a real Chrome browser's TLS/JA3 fingerprint."""
    return AsyncSession(
        impersonate=impersonate,
        proxies={"http": proxy, "https": proxy} if proxy else None,
        allow_redirects=True,
    )


# ---------------------------------------------------------------------------
# Shared Playwright browser pool (singleton)
# ---------------------------------------------------------------------------

import asyncio

_browser: Optional["PlaywrightBrowser"] = None  # type: ignore[name-defined]
_browser_lock = asyncio.Lock()


async def launch_browser(
    executable_path: str = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    headless: bool = True,
) -> "PlaywrightBrowser":  # type: ignore[name-defined]
    """Return a shared Playwright browser instance across all scraper calls."""
    global _browser
    async with _browser_lock:
        if _browser is None or not _browser.is_connected():
            try:
                from playwright.async_api import async_playwright  # noqa: E402
            except ImportError as exc:
                raise RuntimeError(
                    "playwright is required for browser fallback: pip install playwright"
                ) from exc
            pw = await async_playwright().start()
            _browser = await pw.chromium.launch(
                headless=headless,
                executable_path=executable_path,
                args=[
                    "--disable-blink-features=AutomationControlled",
                    "--no-sandbox",
                    "--disable-dev-shm-usage",
                ],
            )
    return _browser


async def close_browser() -> None:
    global _browser
    if _browser is not None and _browser.is_connected():
        await _browser.close()
        _browser = None