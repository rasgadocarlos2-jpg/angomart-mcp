"""
Playwright-based fallback for AngoMart endpoints that are anti-bot-protected
when called directly (catalogue/get, get_products_for_category).

Only loaded if `playwright` is installed. One persistent context per process,
re-used across calls to amortize startup cost.

Install:
    pip install playwright
    playwright install chromium
"""
from __future__ import annotations

import asyncio
import json
from typing import Any, Optional

try:
    from playwright.async_api import async_playwright, Browser, BrowserContext, Page
    HAS_PLAYWRIGHT = True
except ImportError:
    HAS_PLAYWRIGHT = False

_browser: Optional["Browser"] = None
_context: Optional["BrowserContext"] = None
_page: Optional["Page"] = None
_pw = None
_lock = asyncio.Lock()


async def _ensure_page(seed_url: str) -> "Page":
    global _pw, _browser, _context, _page
    if _page is not None:
        return _page
    _pw = await async_playwright().start()
    _browser = await _pw.chromium.launch(headless=True)
    _context = await _browser.new_context(
        user_agent=(
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        ),
        locale="pt-PT",
    )
    _page = await _context.new_page()
    await _page.goto(seed_url, wait_until="domcontentloaded", timeout=30000)
    # Wait for the app to call its init endpoints
    await _page.wait_for_timeout(2000)
    return _page


async def browser_fetch_json(
    seed_url: str,
    path: str,
    method: str = "POST",
    body: Optional[dict] = None,
) -> dict[str, Any]:
    """
    Run a fetch() inside the AngoMart page context. Carries the page's
    cookies, origin, and any JS-set fingerprint, which bypasses the
    status:100 anti-bot response on /api/catalogue/get and friends.
    """
    if not HAS_PLAYWRIGHT:
        raise RuntimeError(
            "playwright is not installed. Run: pip install playwright && playwright install chromium"
        )

    async with _lock:
        page = await _ensure_page(seed_url)
        result = await page.evaluate(
            """async ({path, method, body}) => {
                const opts = {method, headers: {'Content-Type': 'application/json'}};
                if (body) opts.body = JSON.stringify(body);
                const r = await fetch(path, opts);
                return await r.json();
            }""",
            {"path": path, "method": method, "body": body},
        )
        return result


async def close_browser() -> None:
    global _pw, _browser, _context, _page
    if _page:
        await _page.close()
        _page = None
    if _context:
        await _context.close()
        _context = None
    if _browser:
        await _browser.close()
        _browser = None
    if _pw:
        await _pw.stop()
        _pw = None
