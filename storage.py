"""Armazenamento local para listas de compras e orçamentos.

Usa SQLite num ficheiro local (`~/.angomart-mcp/history.db`), sem rede. É
persistente entre sessões do MCP server e seguro para múltiplos processos
(SQLite com modo WAL).
"""
from __future__ import annotations

import json
import os
import sqlite3
import time
from pathlib import Path
from typing import Any, Optional

_DB_PATH_ENV = "ANGOMART_DB_PATH"


def _db_path() -> Path:
    override = os.getenv(_DB_PATH_ENV, "").strip()
    if override:
        path = Path(override)
    else:
        path = Path.home() / ".angomart-mcp" / "history.db"
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(_db_path())
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.row_factory = sqlite3.Row
    _ensure_schema(conn)
    return conn


def _ensure_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS shopping_lists (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL UNIQUE,
            store_id INTEGER,
            items_json TEXT NOT NULL,
            created_at INTEGER NOT NULL,
            updated_at INTEGER NOT NULL
        );
        CREATE TABLE IF NOT EXISTS budgets (
            period TEXT PRIMARY KEY,
            limit_kz REAL NOT NULL,
            notes TEXT,
            updated_at INTEGER NOT NULL
        );
        CREATE TABLE IF NOT EXISTS budget_entries (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            period TEXT NOT NULL,
            label TEXT NOT NULL,
            amount_kz REAL NOT NULL,
            created_at INTEGER NOT NULL,
            FOREIGN KEY (period) REFERENCES budgets(period) ON DELETE CASCADE
        );
        CREATE INDEX IF NOT EXISTS idx_entries_period ON budget_entries(period);
        """
    )
    conn.commit()


def save_shopping_list(name: str, store_id: Optional[int], items: list[dict]) -> dict:
    now = int(time.time())
    items_json = json.dumps(items, ensure_ascii=False)
    with _connect() as conn:
        existing = conn.execute("SELECT id, created_at FROM shopping_lists WHERE name = ?", (name,)).fetchone()
        if existing:
            conn.execute(
                "UPDATE shopping_lists SET store_id = ?, items_json = ?, updated_at = ? WHERE id = ?",
                (store_id, items_json, now, existing["id"]),
            )
            created_at = existing["created_at"]
        else:
            conn.execute(
                "INSERT INTO shopping_lists (name, store_id, items_json, created_at, updated_at) VALUES (?, ?, ?, ?, ?)",
                (name, store_id, items_json, now, now),
            )
            created_at = now
        conn.commit()
    return {"name": name, "store_id": store_id, "items_count": len(items), "created_at": created_at, "updated_at": now}


def load_shopping_list(name: str) -> Optional[dict]:
    with _connect() as conn:
        row = conn.execute("SELECT * FROM shopping_lists WHERE name = ?", (name,)).fetchone()
    if not row:
        return None
    return {
        "name": row["name"],
        "store_id": row["store_id"],
        "items": json.loads(row["items_json"]),
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }


def list_shopping_lists() -> list[dict]:
    with _connect() as conn:
        rows = conn.execute(
            "SELECT name, store_id, items_json, created_at, updated_at FROM shopping_lists ORDER BY updated_at DESC"
        ).fetchall()
    return [
        {
            "name": r["name"],
            "store_id": r["store_id"],
            "items_count": len(json.loads(r["items_json"])),
            "updated_at": r["updated_at"],
        }
        for r in rows
    ]


def delete_shopping_list(name: str) -> bool:
    with _connect() as conn:
        cur = conn.execute("DELETE FROM shopping_lists WHERE name = ?", (name,))
        conn.commit()
        return cur.rowcount > 0


def set_budget(period: str, limit_kz: float, notes: Optional[str] = None) -> dict:
    now = int(time.time())
    with _connect() as conn:
        conn.execute(
            "INSERT INTO budgets (period, limit_kz, notes, updated_at) VALUES (?, ?, ?, ?) "
            "ON CONFLICT(period) DO UPDATE SET limit_kz = excluded.limit_kz, notes = excluded.notes, updated_at = excluded.updated_at",
            (period, limit_kz, notes, now),
        )
        conn.commit()
    return {"period": period, "limit_kz": limit_kz, "notes": notes}


def record_budget_entry(period: str, label: str, amount_kz: float) -> dict:
    now = int(time.time())
    with _connect() as conn:
        # Garantir que o budget existe (criar vazio se não existir)
        conn.execute(
            "INSERT OR IGNORE INTO budgets (period, limit_kz, notes, updated_at) VALUES (?, 0, 'auto-criado', ?)",
            (period, now),
        )
        cur = conn.execute(
            "INSERT INTO budget_entries (period, label, amount_kz, created_at) VALUES (?, ?, ?, ?)",
            (period, label, amount_kz, now),
        )
        conn.commit()
        entry_id = cur.lastrowid
    return {"id": entry_id, "period": period, "label": label, "amount_kz": amount_kz}


def budget_status(period: str) -> dict:
    with _connect() as conn:
        budget = conn.execute("SELECT * FROM budgets WHERE period = ?", (period,)).fetchone()
        entries = conn.execute(
            "SELECT id, label, amount_kz, created_at FROM budget_entries WHERE period = ? ORDER BY created_at",
            (period,),
        ).fetchall()
    spent = sum(e["amount_kz"] for e in entries)
    limit_kz = budget["limit_kz"] if budget else 0.0
    return {
        "period": period,
        "limit_kz": limit_kz,
        "spent_kz": spent,
        "remaining_kz": limit_kz - spent,
        "pct_used": (spent / limit_kz * 100) if limit_kz > 0 else None,
        "entries": [
            {"id": e["id"], "label": e["label"], "amount_kz": e["amount_kz"], "created_at": e["created_at"]}
            for e in entries
        ],
        "notes": budget["notes"] if budget else None,
    }


# Receita base para 1 pessoa/semana — proporções típicas para plano mensal (×4 semanas × N pessoas).
# Quantidades em "unidades" tal como aparecem no catálogo (kg, L, embalagem).
MONTHLY_BASE_PER_PERSON_PER_WEEK = [
    # Proteína
    {"name": "peito frango", "quantity": 0.5, "category": "proteina"},
    {"name": "frango", "quantity": 0.5, "category": "proteina"},
    {"name": "ovos", "quantity": 0.5, "category": "proteina"},
    {"name": "linguiça", "quantity": 0.25, "category": "proteina"},
    {"name": "peixe", "quantity": 0.5, "category": "proteina"},
    # Lácteos
    {"name": "leite", "quantity": 1, "category": "lacteos"},
    {"name": "iogurte", "quantity": 0.5, "category": "lacteos"},
    # Hidratos
    {"name": "arroz", "quantity": 0.5, "category": "hidratos"},
    {"name": "massa", "quantity": 0.25, "category": "hidratos"},
    {"name": "batata", "quantity": 1, "category": "hidratos"},
    {"name": "pão", "quantity": 0.5, "category": "hidratos"},
    # Legumes
    {"name": "cenoura", "quantity": 0.5, "category": "legumes"},
    {"name": "cebola", "quantity": 0.5, "category": "legumes"},
    {"name": "tomate", "quantity": 0.5, "category": "legumes"},
    # Fruta
    {"name": "maçã", "quantity": 0.5, "category": "fruta"},
    {"name": "banana", "quantity": 0.5, "category": "fruta"},
    {"name": "laranja", "quantity": 0.5, "category": "fruta"},
    # Básicos
    {"name": "óleo", "quantity": 0.25, "category": "basicos"},
    {"name": "açúcar", "quantity": 0.25, "category": "basicos"},
    {"name": "sal", "quantity": 0.1, "category": "basicos"},
]


def monthly_shopping_items(people: int, weeks: int = 4) -> list[dict]:
    """Gera a lista base para N pessoas × N semanas, somando quantidades por nome."""
    multiplier = people * weeks
    aggregated: dict[str, float] = {}
    for item in MONTHLY_BASE_PER_PERSON_PER_WEEK:
        name = item["name"]
        aggregated[name] = aggregated.get(name, 0.0) + item["quantity"] * multiplier
    return [{"name": name, "quantity": round(qty, 2)} for name, qty in aggregated.items()]
