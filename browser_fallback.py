"""Playwright fallback for AngoMart endpoints protected against direct HTTP."""
from __future__ import annotations

import asyncio
from typing import Any, Optional
from urllib.parse import urlparse

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


async def _close_unlocked() -> None:
    global _pw, _browser, _context, _page
    for resource, method in ((_page, "close"), (_context, "close"), (_browser, "close"), (_pw, "stop")):
        if resource is not None:
            try:
                await getattr(resource, method)()
            except Exception:
                pass
    _page = _context = _browser = _pw = None


async def _ensure_page(seed_url: str) -> "Page":
    global _pw, _browser, _context, _page
    if _page is not None:
        try:
            if _page.is_closed():
                await _close_unlocked()
        except Exception:
            await _close_unlocked()
    if _page is None:
        _pw = await async_playwright().start()
        _browser = await _pw.chromium.launch(headless=True)
        _context = await _browser.new_context(
            user_agent=("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"),
            locale="pt-PT",
        )
        _page = await _context.new_page()
    parsed = urlparse(seed_url)
    if parsed.scheme != "https" or parsed.netloc != "www.angomart.com":
        raise ValueError("O fallback do browser só aceita URLs oficiais do AngoMart.")
    if _page.url != seed_url:
        await _page.goto(seed_url, wait_until="domcontentloaded", timeout=30000)
        await _page.wait_for_timeout(1200)
    return _page


async def browser_fetch_json(seed_url: str, path: str, method: str = "POST",
                             body: Optional[dict] = None) -> dict[str, Any]:
    """Executa fetch dentro do contexto autenticado da página AngoMart."""
    if not HAS_PLAYWRIGHT:
        raise RuntimeError("Playwright indisponível. Instala com: pip install 'angomart-mcp[browser]' && playwright install chromium")
    if not path.startswith("/api/"):
        raise ValueError("Endpoint inválido para o fallback.")
    async with _lock:
        page = await _ensure_page(seed_url)
        try:
            result = await page.evaluate(
                """async ({path, method, body}) => {
                    const opts = {method, headers: {'Content-Type': 'application/json'}};
                    if (body && method !== 'GET') opts.body = JSON.stringify(body);
                    const response = await fetch(path, opts);
                    const text = await response.text();
                    let data;
                    try { data = JSON.parse(text); }
                    catch (_) { throw new Error('Resposta não JSON (HTTP ' + response.status + ')'); }
                    if (!response.ok) throw new Error('HTTP ' + response.status);
                    return data;
                }""",
                {"path": path, "method": method.upper(), "body": body},
            )
            return result
        except Exception:
            # A página pode ter ficado inválida; recriar no próximo pedido.
            await _close_unlocked()
            raise


async def close_browser() -> None:
    """Fecha recursos Playwright; chamar no encerramento do servidor."""
    async with _lock:
        await _close_unlocked()
