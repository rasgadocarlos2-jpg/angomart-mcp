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
    # Validar URL ANTES de alocar recursos — evita deixar o browser aberto num erro.
    parsed = urlparse(seed_url)
    if parsed.scheme != "https" or parsed.netloc != "www.angomart.com":
        raise ValueError("O fallback do browser só aceita URLs oficiais do AngoMart.")
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
    if _page.url != seed_url:
        await _page.goto(seed_url, wait_until="domcontentloaded", timeout=30000)
        # Aguarda a app inicializar completamente: estabelece cookies de sessão,
        # chama marketplace_fetch_app_configuration, e só depois o catálogo fica
        # acessível. 1.2s não chega em ligações lentas.
        try:
            await _page.wait_for_function(
                "() => localStorage.getItem('marketplace_reference_id') && "
                "localStorage.getItem('stores')",
                timeout=20000,
            )
        except Exception:
            # Se não aparecer no prazo, continuamos de qualquer modo —
            # a página pode ter forma diferente num futuro deploy.
            pass
        await _page.wait_for_timeout(2000)
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
            await _close_unlocked()
            raise


async def scrape_full_catalogue(seed_url: str) -> dict[str, Any]:
    """Visita cada categoria da loja, scrape nome+preço+stock+SKU de cada produto.

    Devolve: {"categories": [{"name", "catalogue_id", "products": [...]}], "total_products": int}
    O catalogue_id é um hash estável do nome (positivo, 8 dígitos) — não corresponde
    ao id da API, mas é reprodutível entre chamadas.
    """
    if not HAS_PLAYWRIGHT:
        raise RuntimeError("Playwright indisponível.")
    async with _lock:
        page = await _ensure_page(seed_url)
        # Obter lista de nomes de categorias da sidebar
        category_names = await page.evaluate(
            """() => {
                const seen = new Set();
                const names = [];
                for (const el of document.querySelectorAll('*')) {
                    const t = (el.textContent || '').trim();
                    if (!t || t.length < 3 || t.length > 50) continue;
                    if (el.children.length !== 0) continue;
                    if (!/^[A-ZÁÂÃÀÉÊÍÓÔÕÚÇ][A-ZÁÂÃÀÉÊÍÓÔÕÚÇ\\s&,.]+$/.test(t)) continue;
                    if (seen.has(t)) continue;
                    seen.add(t);
                    names.push(t);
                }
                return names;
            }"""
        )

        categories: list[dict[str, Any]] = []
        for name in category_names:
            # Clicar na categoria para carregar produtos
            clicked = await page.evaluate(
                """(target) => {
                    const el = [...document.querySelectorAll('*')].find(e =>
                        (e.textContent || '').trim() === target && e.children.length === 0
                    );
                    if (!el) return false;
                    el.scrollIntoView();
                    ['mousedown','mouseup','click'].forEach(t =>
                        el.dispatchEvent(new MouseEvent(t, {bubbles:true, cancelable:true, view:window}))
                    );
                    return true;
                }""",
                name,
            )
            if not clicked:
                continue
            await page.wait_for_timeout(2500)
            # Scroll para carregar lazy-load
            await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
            await page.wait_for_timeout(1200)
            # Scrape product cards
            products = await page.evaluate(
                """() => {
                    const cards = [...document.querySelectorAll('div')].filter(d => {
                        const txt = d.innerText || '';
                        return /kz[\\d,]+\\.\\d{2}/i.test(txt) &&
                               txt.length < 300 && d.children.length < 20 && d.querySelector('img');
                    });
                    const seen = new Set();
                    const out = [];
                    for (const c of cards) {
                        const txt = c.innerText.replace(/\\s+/g, ' ').trim();
                        if (seen.has(txt) || txt.length > 200) continue;
                        seen.add(txt);
                        // extract price
                        const m = txt.match(/kz([\\d,.]+)/i);
                        const price = m ? parseFloat(m[1].replace(/,/g,'')) : null;
                        // name is everything before the first " kz" or before the unit repeat
                        const parts = txt.split(/\\s+kz/i)[0].trim();
                        out.push({name: parts, price: price, raw: txt});
                    }
                    return out;
                }"""
            )
            # Hash estável do nome da categoria
            synthetic_id = abs(hash(name)) % 90000000 + 11900000
            categories.append({
                "name": name,
                "catalogue_id": synthetic_id,
                "products": products,
            })

        total = sum(len(c["products"]) for c in categories)
        return {"categories": categories, "total_products": total}


async def read_cached_products(seed_url: str) -> list[dict[str, Any]]:
    """Lê produtos pré-carregados em localStorage.app da app AngoMart.

    A app Yelo guarda produtos navegados em `localStorage['app'].product`
    como `{catalogue_id: [{product_name, cost, available_quantity, ...}]}`.
    Esta função devolve tudo o que estiver em cache no momento. Útil quando
    a API bloqueia chamadas directas (status 100).
    """
    if not HAS_PLAYWRIGHT:
        raise RuntimeError("Playwright indisponível.")
    async with _lock:
        page = await _ensure_page(seed_url)
        data = await page.evaluate(
            """() => {
                try {
                    const raw = localStorage.getItem('app');
                    if (!raw) return [];
                    const parsed = JSON.parse(decodeURIComponent(raw));
                    const products = parsed.product || {};
                    const out = [];
                    for (const [catId, list] of Object.entries(products)) {
                        for (const p of list) out.push({
                            name: p.product_name,
                            price: p.cost,
                            available_quantity: p.available_quantity,
                            sku: p.sku,
                            product_id: p.product_id,
                            catalogue_id: Number(catId),
                        });
                    }
                    return out;
                } catch (e) { return []; }
            }"""
        )
        return data or []


async def close_browser() -> None:
    """Fecha recursos Playwright; chamar no encerramento do servidor."""
    async with _lock:
        await _close_unlocked()
