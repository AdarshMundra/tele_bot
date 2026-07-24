"""Database layer — asyncpg connection pool + all SQL operations."""
from __future__ import annotations

import asyncpg
from decimal import Decimal
from datetime import datetime
from typing import Any, Optional

from app.config import settings


_pool: Optional[asyncpg.Pool] = None


async def init_pool() -> None:
    global _pool
    _pool = await asyncpg.create_pool(
        dsn=settings.supabase_db_url,
        min_size=1,
        max_size=5,
        statement_cache_size=0,  # required for Supabase PgBouncer
    )


async def close_pool() -> None:
    global _pool
    if _pool:
        await _pool.close()
        _pool = None


def get_pool() -> asyncpg.Pool:
    if _pool is None:
        raise RuntimeError("DB pool not initialised")
    return _pool


# ---------------------------------------------------------------------------
# Write operations
# ---------------------------------------------------------------------------

async def insert_expense(
    amount: Decimal,
    description: str,
    category: str,
    subcategory: str,
    occurred_at: datetime,
    raw_message: str,
    payment_mode: Optional[str] = None,
    payment_source: Optional[str] = None,
) -> int:
    """Insert an expense row and return its id."""
    row = await get_pool().fetchrow(
        """
        INSERT INTO expenses (amount, description, category, subcategory, occurred_at, raw_message, payment_mode, payment_source)
        VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
        RETURNING id
        """,
        amount,
        description,
        category,
        subcategory,
        occurred_at,
        raw_message,
        payment_mode,
        payment_source,
    )
    return row["id"]


async def delete_expense(expense_id: int) -> bool:
    """Delete expense by id. Returns True if a row was deleted."""
    result = await get_pool().execute(
        "DELETE FROM expenses WHERE id = $1", expense_id
    )
    return result == "DELETE 1"


async def get_last_expense() -> Optional[dict[str, Any]]:
    """Return the most recently created expense row."""
    row = await get_pool().fetchrow(
        "SELECT * FROM expenses ORDER BY created_at DESC LIMIT 1"
    )
    return dict(row) if row else None


async def get_expense_by_id(expense_id: int) -> Optional[dict[str, Any]]:
    row = await get_pool().fetchrow(
        "SELECT * FROM expenses WHERE id = $1", expense_id
    )
    return dict(row) if row else None


# ---------------------------------------------------------------------------
# Report queries — all date boundaries in IST (passed as aware datetimes)
# ---------------------------------------------------------------------------

async def get_daily_report(day_start: datetime, day_end: datetime) -> dict[str, Any]:
    """Return total and per-category breakdown for a given IST day range."""
    rows = await get_pool().fetch(
        """
        SELECT category, SUM(amount) AS total
        FROM expenses
        WHERE occurred_at >= $1 AND occurred_at < $2
        GROUP BY category
        ORDER BY total DESC
        """,
        day_start,
        day_end,
    )
    grand_total = sum(r["total"] for r in rows)
    breakdown = [{"category": r["category"], "total": r["total"]} for r in rows]
    return {"total": grand_total, "breakdown": breakdown}


async def get_monthly_report(month_start: datetime, month_end: datetime) -> dict[str, Any]:
    """Return total + per-category breakdown for a calendar month range."""
    rows = await get_pool().fetch(
        """
        SELECT category, SUM(amount) AS total
        FROM expenses
        WHERE occurred_at >= $1 AND occurred_at < $2
        GROUP BY category
        ORDER BY total DESC
        """,
        month_start,
        month_end,
    )
    grand_total = sum(r["total"] for r in rows)
    breakdown = [{"category": r["category"], "total": r["total"]} for r in rows]
    return {"total": grand_total, "breakdown": breakdown}


async def get_weekly_report(week_start: datetime, week_end: datetime) -> dict[str, Any]:
    rows = await get_pool().fetch(
        """
        SELECT category, SUM(amount) AS total
        FROM expenses
        WHERE occurred_at >= $1 AND occurred_at < $2
        GROUP BY category
        ORDER BY total DESC
        """,
        week_start,
        week_end,
    )
    grand_total = sum(r["total"] for r in rows)
    breakdown = [{"category": r["category"], "total": r["total"]} for r in rows]
    return {"total": grand_total, "breakdown": breakdown}


async def get_daily_totals_for_week(week_start: datetime, week_end: datetime) -> list[dict]:
    rows = await get_pool().fetch(
        """
        SELECT DATE(occurred_at AT TIME ZONE 'Asia/Kolkata') AS day, SUM(amount) AS total
        FROM expenses
        WHERE occurred_at >= $1 AND occurred_at < $2
        GROUP BY day
        ORDER BY day
        """,
        week_start,
        week_end,
    )
    return [{"day": r["day"], "total": r["total"]} for r in rows]


async def get_payment_breakdown(start: datetime, end: datetime) -> dict[str, Any]:
    mode_rows = await get_pool().fetch(
        """
        SELECT payment_mode AS mode, SUM(amount) AS total
        FROM expenses
        WHERE occurred_at >= $1 AND occurred_at < $2 AND payment_mode IS NOT NULL
        GROUP BY payment_mode
        ORDER BY total DESC
        """,
        start,
        end,
    )
    source_rows = await get_pool().fetch(
        """
        SELECT payment_source AS source, SUM(amount) AS total
        FROM expenses
        WHERE occurred_at >= $1 AND occurred_at < $2 AND payment_source IS NOT NULL
        GROUP BY payment_source
        ORDER BY total DESC
        """,
        start,
        end,
    )
    return {
        "by_mode": [{"mode": r["mode"], "total": r["total"]} for r in mode_rows],
        "by_source": [{"source": r["source"], "total": r["total"]} for r in source_rows],
    }


async def get_subcategory_breakdown(start: datetime, end: datetime) -> list[dict]:
    rows = await get_pool().fetch(
        """
        SELECT category, subcategory, SUM(amount) AS total
        FROM expenses
        WHERE occurred_at >= $1 AND occurred_at < $2
        GROUP BY category, subcategory
        ORDER BY category, total DESC
        """,
        start,
        end,
    )
    return [{"category": r["category"], "subcategory": r["subcategory"], "total": r["total"]} for r in rows]


async def get_monthly_report_last_month(month_start: datetime, month_end: datetime) -> Decimal:
    """Return total spending for the previous month (for comparison)."""
    row = await get_pool().fetchrow(
        """
        SELECT COALESCE(SUM(amount), 0) AS total
        FROM expenses
        WHERE occurred_at >= $1 AND occurred_at < $2
        """,
        month_start,
        month_end,
    )
    return row["total"]
