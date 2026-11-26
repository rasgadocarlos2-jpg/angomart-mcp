# angomart-mcp

[![MCP](https://img.shields.io/badge/MCP-compatible-5A67D8?logo=anthropic&logoColor=white)](https://modelcontextprotocol.io)
[![Python](https://img.shields.io/badge/Python-3.10%2B-3776AB?logo=python&logoColor=white)](https://www.python.org/)
[![License: MIT](https://img.shields.io/badge/License-MIT-green.svg)](LICENSE)

Servidor MCP não-oficial para o **AngoMart** (Angola). Permite consultar lojas, categorias, produtos, preços e stock publicados no catálogo. Inclui ferramentas para estimar listas de compras e comparar preços entre lojas.

> Projecto independente do AngoMart e da Jungleworks. Depende de endpoints não documentados, que podem mudar. Não efectua compras nem garante disponibilidade no checkout.

## Ferramentas

| Ferramenta | Função |
|---|---|
| `angomart_list_stores` | Lista lojas por coordenadas (Luanda/Talatona por omissão) |
| `angomart_list_categories` | Lista categorias e subcategorias de uma loja |
| `angomart_get_products_by_category` | Consulta produtos de uma categoria, com paginação limitada |
| `angomart_search_products` | Pesquisa por nome, ignorando acentos |
| `angomart_get_product_details` | Procura correspondências e apresenta preço, stock e SKU disponíveis |
| `angomart_calculate_shopping_list` | Calcula uma estimativa de custo para uma lista JSON |
| `angomart_compare_stores` | Compara resultados de um produto em várias lojas |
| `angomart_save_list` | Guarda uma lista de compras localmente (SQLite) |
| `angomart_load_list` | Carrega uma lista guardada (opcionalmente recalcula preços) |
| `angomart_list_saved_lists` | Enumera todas as listas guardadas |
| `angomart_delete_list` | Apaga uma lista guardada |
| `angomart_plan_monthly_shopping` | Plano mensal equilibrado para N pessoas × N semanas |
| `angomart_budget_set` | Define o orçamento de um período (ex. '2026-11') |
| `angomart_budget_record` | Regista uma despesa no período |
| `angomart_budget_status` | Mostra limite, gasto, restante, lista de despesas |

## Instalação

Requer Python 3.10+.

```bash
git clone https://github.com/rasgadocarlos2-jpg/angomart-mcp.git
cd angomart-mcp
python -m venv .venv
source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -e .
```

O Playwright é opcional. Para activar o fallback quando a API responde com `status: 100`:

```bash
pip install -e ".[browser]"
playwright install chromium
```

Também podes definir:
- `ANGOMART_COOKIE`: cookie de sessão do site, se necessário.
- `ANGOMART_CACHE_TTL`: cache em segundos (predefinição: 180).
- `ANGOMART_MAX_PAGES`: máximo de páginas percorridas por categoria na pesquisa (predefinição: 5).
- `ANGOMART_TIMEOUT`: timeout HTTP em segundos (predefinição: 25).
- `ANGOMART_DB_PATH`: caminho do SQLite local (predefinição: `~/.angomart-mcp/history.db`).

## Configuração num cliente MCP

Exemplo de configuração local:

```json
{
  "mcpServers": {
    "angomart": {
      "command": "python",
      "args": ["/caminho/completo/para/angomart-mcp/server.py"]
    }
  }
}
```

Reinicia o cliente MCP depois de guardar a configuração.

## Exemplos

Pesquisa de produtos:

```text
angomart_search_products(store_id=1736945, query="arroz")
```

Estimativa de lista de compras (quantidade multiplica o preço da embalagem/unidade apresentada):

```text
angomart_calculate_shopping_list(
  store_id=1736945,
  items_json='[{"name":"arroz","quantity":2},{"name":"feijão","quantity":1}]'
)
```

Comparação entre lojas:

```text
angomart_compare_stores(store_ids="1736945,1736946", query="leite")
```

## Como funciona e limitações

O servidor consulta endpoints JSON usados pelo site AngoMart (plataforma Yelo/Jungleworks). Esses endpoints não são uma API pública documentada nem têm contrato estável.

- Os preços e o stock são os valores devolvidos pelo catálogo no momento da consulta; podem mudar e não equivalem a confirmação de checkout.
- A pesquisa percorre categorias/subcategorias e páginas até ao limite configurado; pode ser lenta ou não encontrar produtos para além desse limite.
- O cálculo da lista multiplica a quantidade pelo preço da embalagem/unidade listada. **Não converte kg, litros ou tamanhos de embalagem**; confirma sempre a unidade e a variante.
- A comparação só é válida quando marca, peso, volume e variante correspondem.
- Não faz login, não adiciona artigos ao carrinho e não submete encomendas.
- O fallback Playwright só é tentado quando o endpoint devolve `status: 100`. A estrutura da API pode mudar e bloquear a integração.

## Desenvolvimento

```bash
pip install -e ".[dev]"
pytest
ruff check .
```

Ainda não há testes de integração contra o AngoMart: esses testes devem ser executados com cuidado e sem automatizar compras.

## Licença

MIT. Consulta [LICENSE](LICENSE).
