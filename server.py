#!/usr/bin/env python3
"""
AngoMart MCP Server
Consulta lojas, categorias e produtos do AngoMart (Angola) via API JSON.
Não-oficial. Sem afiliação com AngoMart ou Yelo/Jungleworks.
"""
import os
from typing import Optional

import httpx
from mcp.server.fastmcp import FastMCP

mcp = FastMCP("angomart")

BASE_URL = "https://www.angomart.com"
MARKETPLACE_USER_ID = 1729112
DOMAIN = "www.angomart.com"

DEFAULT_LAT = -8.914876
DEFAULT_LNG = 13.203437

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36"
    ),
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "pt-PT,pt;q=0.9,en;q=0.8",
    "Origin": BASE_URL,
    "Referer": f"{BASE_URL}/",
    "X-Requested-With": "XMLHttpRequest",
}

_COOKIE_OVERRIDE = os.environ.get("ANGOMART_COOKIE", "").strip()
if _COOKIE_OVERRIDE:
    HEADERS["Cookie"] = _COOKIE_OVERRIDE

_client: Optional[httpx.AsyncClient] = None
_marketplace_ref: Optional[str] = None


async def _get_client() -> httpx.AsyncClient:
    global _client, _marketplace_ref
    if _client is None:
        _client = httpx.AsyncClient(timeout=25.0, headers=HEADERS, follow_redirects=True)
        # Warmup: obter marketplace_reference_id (Yelo exige-o em todas as chamadas)
        r = await _client.get(
            f"{BASE_URL}/api/marketplace_fetch_app_configuration",
            params={
                "domain_name": DOMAIN,
                "post_to_get": 1,
                "dual_user_key": 0,
                "language": "pt",
            },
        )
        try:
            j = r.json()
            data = j.get("data") or {}
            _marketplace_ref = (
                data.get("marketplace_reference_id")
                or data.get("marketplace", {}).get("marketplace_reference_id")
            )
        except Exception:
            _marketplace_ref = None
    return _client


async def _get(path: str, params: dict) -> dict:
    client = await _get_client()
    params = {**params}
    if _marketplace_ref and "marketplace_reference_id" not in params:
        params["marketplace_reference_id"] = _marketplace_ref
    r = await client.get(f"{BASE_URL}{path}", params=params)
    r.raise_for_status()
    return r.json()


async def _post(path: str, body: dict) -> dict:
    client = await _get_client()
    body = {**body}
    if _marketplace_ref and "marketplace_reference_id" not in body:
        body["marketplace_reference_id"] = _marketplace_ref
    r = await client.post(f"{BASE_URL}{path}", json=body)
    r.raise_for_status()
    return r.json()


def _fmt_price(kz) -> str:
    try:
        return f"{int(float(kz)):,} Kz".replace(",", ".")
    except Exception:
        return f"{kz} Kz"


@mcp.tool()
async def angomart_list_stores(
    latitude: float = DEFAULT_LAT,
    longitude: float = DEFAULT_LNG,
    limit: int = 20,
) -> str:
    """Lista lojas AngoMart ordenadas por proximidade das coordenadas (Luanda/Talatona por omissão)."""
    data = await _get(
        "/api/marketplace/marketplace_get_city_storefronts_v3",
        {
            "domain_name": DOMAIN,
            "post_to_get": 1,
            "marketplace_user_id": MARKETPLACE_USER_ID,
            "latitude": latitude,
            "longitude": longitude,
            "search_text": "",
            "need_eta": 0,
            "limit": limit,
            "source": 0,
            "dual_user_key": 0,
            "language": "pt",
        },
    )
    payload = data.get("data")
    stores = payload if isinstance(payload, list) else (payload or {}).get("storefronts", []) or []
    if not stores:
        return "Nenhuma loja encontrada para essa localização."

    out = ["Lojas AngoMart encontradas:\n"]
    for s in stores:
        name = s.get("name") or s.get("store_name") or s.get("storepage_slug")
        uid = s.get("user_id")
        slug = s.get("storepage_slug", "")
        out.append(f"- {name} (store_id: {uid})")
        if slug:
            out.append(f"  URL: {BASE_URL}/en/store/{slug}/{uid}")
    return "\n".join(out)


@mcp.tool()
async def angomart_list_categories(store_id: int) -> str:
    """Árvore de categorias de uma loja. Usa `angomart_list_stores` para obter store_id."""
    data = await _post(
        "/api/catalogue/get",
        {
            "domain_name": DOMAIN,
            "marketplace_user_id": MARKETPLACE_USER_ID,
            "user_id": store_id,
            "post_to_get": 1,
            "source": 0,
            "dual_user_key": 0,
            "language": "pt",
        },
    )
    payload = data.get("data") or {}
    cats = payload.get("result") or payload.get("catalogue") or payload.get("categories") or []
    if not cats:
        return f"Nenhuma categoria encontrada (status: {data.get('status')}, msg: {data.get('message')})."

    out = ["Categorias disponíveis (catalogue_id entre parênteses):\n"]
    for c in cats:
        out.append(f"- {c.get('name')} ({c.get('catalogue_id')})")
        for sub in c.get("sub_categories", []) or []:
            out.append(f"    - {sub.get('name')} ({sub.get('catalogue_id')})")
    return "\n".join(out)


@mcp.tool()
async def angomart_get_products_by_category(
    store_id: int,
    catalogue_id: int,
    page: int = 1,
) -> str:
    """Lista produtos de uma categoria. Usa `angomart_list_categories` para obter catalogue_id."""
    data = await _post(
        "/api/get_products_for_category",
        {
            "domain_name": DOMAIN,
            "marketplace_user_id": MARKETPLACE_USER_ID,
            "user_id": store_id,
            "catalogue_id": catalogue_id,
            "page_no": page,
            "post_to_get": 1,
            "source": 0,
            "dual_user_key": 0,
            "language": "pt",
        },
    )
    payload = data.get("data")
    products = payload if isinstance(payload, list) else (payload or {}).get("products", []) or []
    if not products:
        return "Nenhum produto encontrado nessa categoria."

    out = [f"Produtos ({len(products)} resultados, página {page}):\n"]
    for p in products:
        name = p.get("name", "?")
        price = p.get("price", 0)
        stock = p.get("available_quantity", 0)
        sku = p.get("sku", "")
        pid = p.get("product_id")
        out.append(f"- {name}")
        out.append(f"  Preço: {_fmt_price(price)}  |  SKU: {sku}  |  Stock: {stock}  |  product_id: {pid}")
    return "\n".join(out)


@mcp.tool()
async def angomart_search_products(
    store_id: int,
    query: str,
    max_resultados: int = 20,
) -> str:
    """Pesquisa produtos pelo nome em todas as categorias de uma loja (itera categorias; lento)."""
    if len(query.strip()) < 2:
        return "A pesquisa precisa de pelo menos 2 caracteres."

    cat_data = await _post(
        "/api/catalogue/get",
        {
            "domain_name": DOMAIN,
            "marketplace_user_id": MARKETPLACE_USER_ID,
            "user_id": store_id,
            "post_to_get": 1,
            "source": 0,
            "dual_user_key": 0,
            "language": "pt",
        },
    )
    payload = cat_data.get("data") or {}
    cats = payload.get("result") or payload.get("catalogue") or []

    flat: list[int] = []
    for c in cats:
        cid = c.get("catalogue_id")
        if cid:
            flat.append(cid)
        for sub in c.get("sub_categories", []) or []:
            sid = sub.get("catalogue_id")
            if sid:
                flat.append(sid)

    q = query.lower().strip()
    matches: list[dict] = []
    for cid in flat:
        if len(matches) >= max_resultados:
            break
        prod_data = await _post(
            "/api/get_products_for_category",
            {
                "domain_name": DOMAIN,
                "marketplace_user_id": MARKETPLACE_USER_ID,
                "user_id": store_id,
                "catalogue_id": cid,
                "page_no": 1,
                "post_to_get": 1,
                "source": 0,
                "dual_user_key": 0,
                "language": "pt",
            },
        )
        p_payload = prod_data.get("data")
        products = p_payload if isinstance(p_payload, list) else (p_payload or {}).get("products", []) or []
        for p in products:
            if q in (p.get("name", "") or "").lower():
                matches.append(p)
                if len(matches) >= max_resultados:
                    break

    if not matches:
        return f"Nenhum produto encontrado para '{query}' nessa loja."

    out = [f"Resultados para '{query}' ({len(matches)}):\n"]
    for p in matches:
        out.append(f"- {p.get('name')}")
        out.append(
            f"  Preço: {_fmt_price(p.get('price', 0))}  |  "
            f"Stock: {p.get('available_quantity', 0)}  |  "
            f"SKU: {p.get('sku', '')}  |  product_id: {p.get('product_id')}"
        )
    return "\n".join(out)


if __name__ == "__main__":
    mcp.run()
