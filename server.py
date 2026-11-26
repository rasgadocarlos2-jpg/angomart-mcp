#!/usr/bin/env python3
"""AngoMart MCP: consulta de lojas, catálogo, preços e listas de compras.

Servidor não-oficial, apenas de leitura. Não cria carrinhos nem encomendas.
"""
from __future__ import annotations

import asyncio
import json
import os
import time
import unicodedata
from typing import Any, Optional

import httpx
from mcp.server.fastmcp import FastMCP

try:
    from browser_fallback import browser_fetch_json, HAS_PLAYWRIGHT
except ImportError:
    HAS_PLAYWRIGHT = False

    async def browser_fetch_json(*_args: Any, **_kwargs: Any) -> dict:
        raise RuntimeError("Fallback Playwright indisponível")

mcp = FastMCP("angomart")

BASE_URL = "https://www.angomart.com"
DOMAIN = "www.angomart.com"
MARKETPLACE_USER_ID = 1729112
DEFAULT_LAT = -8.914876
DEFAULT_LNG = 13.203437
CACHE_TTL = int(os.getenv("ANGOMART_CACHE_TTL", "180"))
MAX_PAGES = int(os.getenv("ANGOMART_MAX_PAGES", "5"))
TIMEOUT = float(os.getenv("ANGOMART_TIMEOUT", "25"))

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120.0 Safari/537.36",
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "pt-PT,pt;q=0.9,en;q=0.8",
    "Origin": BASE_URL,
    "Referer": f"{BASE_URL}/",
    "X-Requested-With": "XMLHttpRequest",
}
_cookie = os.getenv("ANGOMART_COOKIE", "").strip()
if _cookie:
    HEADERS["Cookie"] = _cookie

_client: Optional[httpx.AsyncClient] = None
_client_lock = asyncio.Lock()
_marketplace_ref: Optional[str] = None
_cache: dict[str, tuple[float, Any]] = {}


def _cached(key: str) -> Any | None:
    entry = _cache.get(key)
    if entry and entry[0] > time.monotonic():
        return entry[1]
    if entry:
        _cache.pop(key, None)
    return None


def _remember(key: str, value: Any) -> Any:
    _cache[key] = (time.monotonic() + CACHE_TTL, value)
    return value


async def _get_client() -> httpx.AsyncClient:
    global _client, _marketplace_ref
    if _client is not None:
        return _client
    async with _client_lock:
        if _client is not None:
            return _client
        _client = httpx.AsyncClient(timeout=TIMEOUT, headers=HEADERS, follow_redirects=True)
        try:
            response = await _client.get(
                f"{BASE_URL}/api/marketplace_fetch_app_configuration",
                params={"domain_name": DOMAIN, "post_to_get": 1, "dual_user_key": 0, "language": "pt"},
            )
            response.raise_for_status()
            payload = response.json().get("data") or {}
            _marketplace_ref = (
                payload.get("marketplace_reference_id")
                or (payload.get("marketplace") or {}).get("marketplace_reference_id")
            )
        except (httpx.HTTPError, ValueError, TypeError):
            # Algumas versões da API funcionam sem marketplace_reference_id.
            _marketplace_ref = None
        return _client


async def _request(method: str, path: str, payload: dict[str, Any], store_id: int | None = None) -> dict:
    client = await _get_client()
    data = dict(payload)
    if _marketplace_ref and "marketplace_reference_id" not in data:
        data["marketplace_reference_id"] = _marketplace_ref
    try:
        if method.upper() == "GET":
            response = await client.get(f"{BASE_URL}{path}", params=data)
        else:
            response = await client.post(f"{BASE_URL}{path}", json=data)
        response.raise_for_status()
        result = response.json()
    except httpx.TimeoutException as exc:
        raise RuntimeError("Tempo limite ao contactar o AngoMart. Tenta novamente.") from exc
    except httpx.HTTPStatusError as exc:
        raise RuntimeError(f"AngoMart respondeu com HTTP {exc.response.status_code}.") from exc
    except (httpx.HTTPError, ValueError) as exc:
        raise RuntimeError(f"Não foi possível obter uma resposta válida do AngoMart: {exc}") from exc

    if result.get("status") == 100:
        # O marketplace_reference_id pode ter expirado; tenta refrescar e repetir uma vez.
        refreshed = await _refresh_marketplace_ref()
        if refreshed and refreshed != data.get("marketplace_reference_id"):
            data["marketplace_reference_id"] = refreshed
            try:
                if method.upper() == "GET":
                    response = await client.get(f"{BASE_URL}{path}", params=data)
                else:
                    response = await client.post(f"{BASE_URL}{path}", json=data)
                response.raise_for_status()
                result = response.json()
            except (httpx.HTTPError, ValueError):
                pass
    if result.get("status") == 100:
        if HAS_PLAYWRIGHT:
            seed = await _seed_url_for_store(store_id)
            result = await browser_fetch_json(seed, path, method, data)
        else:
            raise RuntimeError(
                "O AngoMart bloqueou o pedido directo (status 100). "
                "Instala Playwright/Chromium ou configura ANGOMART_COOKIE."
            )
    return result


async def _seed_url_for_store(store_id: Optional[int]) -> str:
    """Devolve a URL de loja a usar como seed no Playwright, com slug correcto se conhecido."""
    if not store_id:
        return f"{BASE_URL}/"
    # Tenta a cache de slugs primeiro.
    slug = _cached(f"slug:{store_id}")
    if slug:
        return f"{BASE_URL}/en/store/{slug}/{store_id}"
    # Fallback seguro: usar o store_id sem slug se o site aceitar, ou um placeholder.
    # O AngoMart aceita slug incorrecto desde que o store_id resolva; "store" é neutro.
    return f"{BASE_URL}/en/store/store/{store_id}"


async def _refresh_marketplace_ref() -> Optional[str]:
    """Refaz o warmup e devolve o novo marketplace_reference_id (ou None em caso de falha)."""
    global _marketplace_ref
    client = await _get_client()
    try:
        response = await client.get(
            f"{BASE_URL}/api/marketplace_fetch_app_configuration",
            params={"domain_name": DOMAIN, "post_to_get": 1, "dual_user_key": 0, "language": "pt"},
        )
        response.raise_for_status()
        payload = response.json().get("data") or {}
        _marketplace_ref = (
            payload.get("marketplace_reference_id")
            or (payload.get("marketplace") or {}).get("marketplace_reference_id")
        )
        return _marketplace_ref
    except (httpx.HTTPError, ValueError, TypeError):
        return None


def _data_items(response: dict, *keys: str) -> list[dict]:
    data = response.get("data")
    if isinstance(data, list):
        return data
    if not isinstance(data, dict):
        return []
    for key in keys:
        value = data.get(key)
        if isinstance(value, list):
            return value
    return []


def _format_price(value: Any) -> str:
    try:
        number = float(value)
        return f"{number:,.0f} Kz".replace(",", ".")
    except (TypeError, ValueError):
        return f"{value if value is not None else 'Preço indisponível'} Kz"


def _normalise(value: str) -> str:
    decomposed = unicodedata.normalize("NFKD", value.casefold())
    return "".join(ch for ch in decomposed if not unicodedata.combining(ch)).strip()


def _flatten_categories(categories: list[dict]) -> list[dict]:
    flattened: list[dict] = []
    stack = list(reversed(categories))
    seen: set[int] = set()
    while stack:
        category = stack.pop()
        cid = category.get("catalogue_id")
        if cid is not None:
            try:
                numeric_id = int(cid)
            except (TypeError, ValueError):
                numeric_id = None
            if numeric_id is not None and numeric_id not in seen:
                seen.add(numeric_id)
                flattened.append(category)
        children = category.get("sub_categories") or category.get("children") or []
        if isinstance(children, list):
            stack.extend(reversed(children))
    return flattened


async def _get_categories(store_id: int) -> list[dict]:
    key = f"categories:{store_id}"
    cached = _cached(key)
    if cached is not None:
        return cached
    response = await _request("POST", "/api/catalogue/get", {
        "domain_name": DOMAIN, "marketplace_user_id": MARKETPLACE_USER_ID,
        "user_id": store_id, "post_to_get": 1, "source": 0,
        "dual_user_key": 0, "language": "pt",
    }, store_id)
    data = response.get("data") or {}
    categories = (data.get("result") or data.get("catalogue") or data.get("categories") or []) if isinstance(data, dict) else []
    if not categories:
        raise RuntimeError(f"Não foi possível obter categorias (status={response.get('status')}, mensagem={response.get('message')}).")
    return _remember(key, categories)


async def _get_products_page(store_id: int, catalogue_id: int, page: int) -> list[dict]:
    key = f"products:{store_id}:{catalogue_id}:{page}"
    cached = _cached(key)
    if cached is not None:
        return cached
    response = await _request("POST", "/api/get_products_for_category", {
        "domain_name": DOMAIN, "marketplace_user_id": MARKETPLACE_USER_ID,
        "user_id": store_id, "catalogue_id": catalogue_id, "page_no": page,
        "post_to_get": 1, "source": 0, "dual_user_key": 0, "language": "pt",
    }, store_id)
    products = _data_items(response, "products", "result")
    # Só levantamos erro em códigos de erro conhecidos. Uma página vazia com
    # status de sucesso (0/1/200/None) é um resultado legítimo ("sem produtos").
    status = response.get("status")
    if not products and status in (100, "100", -1, "-1"):
        raise RuntimeError(f"Erro ao consultar produtos (status={status}, mensagem={response.get('message')}).")
    return _remember(key, products)


async def _search(store_id: int, query: str, limit: int = 20) -> list[dict]:
    q = _normalise(query)
    if len(q) < 2:
        raise ValueError("A pesquisa precisa de pelo menos 2 caracteres.")
    categories = _flatten_categories(await _get_categories(store_id))
    matches: list[dict] = []
    for category in categories:
        cid = int(category["catalogue_id"])
        first_page_size: Optional[int] = None
        for page in range(1, MAX_PAGES + 1):
            products = await _get_products_page(store_id, cid, page)
            if not products:
                break
            if first_page_size is None:
                first_page_size = len(products)
            for product in products:
                if q in _normalise(str(product.get("name") or "")):
                    product_copy = dict(product)
                    product_copy.setdefault("_catalogue_id", cid)
                    matches.append(product_copy)
                    if len(matches) >= limit:
                        return matches
            # Se a página devolver menos produtos que a primeira, assumimos fim.
            # (O Yelo não expõe metadados de paginação, mas o tamanho de página é consistente.)
            if len(products) < first_page_size:
                break
    return matches


def _format_product(product: dict) -> str:
    return (
        f"- {product.get('name', 'Produto sem nome')} — {_format_price(product.get('price'))}"
        f" | stock: {product.get('available_quantity', 'n/d')}"
        f" | SKU: {product.get('sku') or 'n/d'}"
        f" | product_id: {product.get('product_id') or 'n/d'}"
    )


@mcp.tool()
async def angomart_list_stores(
    latitude: float = DEFAULT_LAT,
    longitude: float = DEFAULT_LNG,
    limit: int = 20,
) -> str:
    """Lista lojas AngoMart próximas das coordenadas indicadas (Luanda/Talatona por omissão)."""
    if not -90 <= latitude <= 90 or not -180 <= longitude <= 180:
        raise ValueError("Coordenadas inválidas.")
    limit = max(1, min(limit, 50))
    response = await _request("GET", "/api/marketplace/marketplace_get_city_storefronts_v3", {
        "domain_name": DOMAIN, "post_to_get": 1, "marketplace_user_id": MARKETPLACE_USER_ID,
        "latitude": latitude, "longitude": longitude, "search_text": "", "need_eta": 0,
        "limit": limit, "source": 0, "dual_user_key": 0, "language": "pt",
    })
    stores = _data_items(response, "storefronts", "result")
    if not stores:
        return "Nenhuma loja encontrada para estas coordenadas."
    lines = ["Lojas AngoMart:"]
    for store in stores[:limit]:
        name = store.get("name") or store.get("store_name") or store.get("storepage_slug") or "Loja"
        sid = store.get("user_id")
        slug = store.get("storepage_slug")
        if sid and slug:
            # Cache o slug para o fallback Playwright usar a URL correcta desta loja.
            _remember(f"slug:{sid}", slug)
        lines.append(f"- {name} (store_id: {sid})" + (f" — {BASE_URL}/en/store/{slug}/{sid}" if slug else ""))
    return "\n".join(lines)


@mcp.tool()
async def angomart_list_categories(store_id: int) -> str:
    """Lista categorias e subcategorias de uma loja AngoMart."""
    categories = _flatten_categories(await _get_categories(store_id))
    return "\n".join(
        [f"Categorias da loja {store_id} (catalogue_id):"] +
        [f"- {c.get('name', 'Categoria')} ({c.get('catalogue_id')})" for c in categories]
    )


@mcp.tool()
async def angomart_get_products_by_category(
    store_id: int,
    catalogue_id: int,
    page: int = 1,
    max_pages: int = 1,
) -> str:
    """Lista produtos de uma categoria; permite consultar várias páginas (máximo configurável)."""
    if page < 1:
        raise ValueError("A página começa em 1.")
    max_pages = max(1, min(max_pages, MAX_PAGES))
    products: list[dict] = []
    first_page_size: Optional[int] = None
    for page_number in range(page, page + max_pages):
        batch = await _get_products_page(store_id, catalogue_id, page_number)
        if not batch:
            break
        if first_page_size is None:
            first_page_size = len(batch)
        products.extend(batch)
        # Páginas subsequentes mais curtas que a primeira indicam fim.
        if len(batch) < first_page_size:
            break
    if not products:
        return "Não foram encontrados produtos nesta categoria/página."
    lines = [f"Produtos da categoria {catalogue_id} — {len(products)} resultado(s):"]
    lines.extend(_format_product(p) for p in products)
    return "\n".join(lines)


@mcp.tool()
async def angomart_search_products(
    store_id: int,
    query: str,
    max_resultados: int = 20,
) -> str:
    """Pesquisa produtos por nome, ignorando acentos e percorrendo subcategorias e páginas."""
    limit = max(1, min(max_resultados, 100))
    products = await _search(store_id, query, limit)
    if not products:
        return f"Não encontrei produtos para '{query}' na loja {store_id}."
    return "\n".join([f"Resultados para '{query}' ({len(products)}):"] + [_format_product(p) for p in products])


@mcp.tool()
async def angomart_get_product_details(store_id: int, query: str) -> str:
    """Procura um produto e devolve os dados de preço, stock, SKU e identificador disponíveis."""
    products = await _search(store_id, query, 10)
    if not products:
        return f"Não encontrei '{query}' na loja {store_id}."
    return "\n".join(["Produtos correspondentes (confirma o tamanho/unidade antes de comprar):"] + [
        _format_product(p) for p in products
    ])


@mcp.tool()
async def angomart_calculate_shopping_list(store_id: int, items_json: str) -> str:
    """Calcula o custo estimado de uma lista.

    items_json é uma lista JSON. Cada entrada tem o campo "name" (obrigatório)
    e, opcionalmente, "quantity" (default 1). Exemplo:
    [ {"name":"arroz","quantity":2}, {"name":"leite","quantity":1} ]

    A quantidade multiplica o preço da embalagem/unidade listada. Quando não há
    match exacto pelo nome, escolhe-se a embalagem MAIS BARATA e assinala-se
    como ambíguo para o utilizador confirmar.
    """
    try:
        items = json.loads(items_json)
    except json.JSONDecodeError as exc:
        raise ValueError("items_json tem de ser uma lista JSON válida.") from exc
    if not isinstance(items, list) or not items:
        raise ValueError("Indica pelo menos um artigo numa lista JSON.")
    if len(items) > 50:
        raise ValueError("O limite é de 50 artigos por lista.")

    # Validar e normalizar todos os itens primeiro, antes de qualquer HTTP.
    normalized: list[tuple[str, float]] = []
    for item in items:
        if not isinstance(item, dict) or not item.get("name"):
            continue
        name = str(item["name"]).strip()
        try:
            quantity = float(item.get("quantity", 1))
        except (TypeError, ValueError):
            raise ValueError(f"Quantidade inválida para {name}.")
        if quantity <= 0 or quantity > 10000:
            raise ValueError(f"Quantidade fora do intervalo para {name}.")
        normalized.append((name, quantity))

    # Pré-carregar categorias uma vez para popular a cache; depois pesquisar em paralelo.
    await _get_categories(store_id)
    search_results = await asyncio.gather(
        *(_search(store_id, name, 5) for name, _ in normalized),
        return_exceptions=True,
    )

    lines = [
        f"Estimativa de compras — loja {store_id}",
        "Nota: quantidade multiplica o preço da embalagem/unidade listada; não converte kg/litros.",
    ]
    total = 0.0
    missing: list[str] = []
    ambiguous: list[str] = []

    for (name, quantity), result in zip(normalized, search_results):
        if isinstance(result, BaseException):
            missing.append(name)
            lines.append(f"- {name}: erro na pesquisa — {result}")
            continue
        matches = result
        if not matches:
            missing.append(name)
            lines.append(f"- {name}: não encontrado")
            continue

        norm_name = _normalise(name)
        exact = [p for p in matches if _normalise(str(p.get("name", ""))) == norm_name]
        if exact:
            chosen = exact[0]
        else:
            # Sem match exacto: escolher a embalagem mais barata (conservador para o orçamento)
            # e avisar o utilizador para confirmar a variante.
            priced = []
            for p in matches:
                try:
                    priced.append((float(p.get("price")), p))
                except (TypeError, ValueError):
                    continue
            if not priced:
                missing.append(name)
                lines.append(f"- {name}: encontrado, mas sem preço legível")
                continue
            priced.sort(key=lambda x: x[0])
            chosen = priced[0][1]
            ambiguous.append(name)

        try:
            unit_price = float(chosen.get("price"))
        except (TypeError, ValueError):
            missing.append(name)
            lines.append(f"- {name}: encontrado, mas sem preço legível")
            continue
        subtotal = unit_price * quantity
        total += subtotal
        marker = " ⚠ confirma variante" if name in ambiguous else ""
        lines.append(
            f"- {chosen.get('name')} × {quantity:g} — {_format_price(unit_price)} cada = {_format_price(subtotal)}{marker}"
        )

    lines.append(f"TOTAL ESTIMADO: {_format_price(total)}")
    if missing:
        lines.append("Artigos por confirmar: " + ", ".join(missing))
    if ambiguous:
        lines.append(
            "Artigos sem match exacto (foi usada a embalagem mais barata): "
            + ", ".join(ambiguous)
            + ". Confirma peso/volume/marca antes de comprar."
        )
    lines.append("Preços e stock podem mudar; isto é uma estimativa, não uma encomenda.")
    return "\n".join(lines)


@mcp.tool()
async def angomart_compare_stores(store_ids: str, query: str) -> str:
    """Compara os primeiros resultados de um produto em várias lojas. store_ids: IDs separados por vírgulas."""
    try:
        ids = [int(value.strip()) for value in store_ids.split(",") if value.strip()]
    except ValueError as exc:
        raise ValueError("store_ids deve conter IDs numéricos separados por vírgulas.") from exc
    if not ids or len(ids) > 10:
        raise ValueError("Indica entre 1 e 10 IDs de lojas.")
    lines = [f"Comparação de '{query}':"]
    for store_id in ids:
        try:
            products = await _search(store_id, query, 5)
            if not products:
                lines.append(f"- Loja {store_id}: não encontrado")
                continue
            lines.append(f"- Loja {store_id}: " + " | ".join(
                f"{p.get('name')} — {_format_price(p.get('price'))}" for p in products[:3]
            ))
        except (RuntimeError, ValueError) as exc:
            lines.append(f"- Loja {store_id}: erro — {exc}")
    lines.append("Confirma se os produtos têm o mesmo peso, volume e marca antes de comparar.")
    return "\n".join(lines)


if __name__ == "__main__":
    mcp.run()
