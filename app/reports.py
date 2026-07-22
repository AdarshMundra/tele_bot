"""Build daily and monthly report strings and compute IST date boundaries."""
from __future__ import annotations

from datetime import datetime, timedelta
from decimal import Decimal
from typing import Any

import pytz

from app.config import settings
from app.db import get_daily_report, get_monthly_report, get_monthly_report_last_month

IST = pytz.timezone(settings.timezone)


def _ist_day_bounds(reference: datetime) -> tuple[datetime, datetime]:
    """Return (start_of_day, start_of_next_day) in UTC for the IST day containing `reference`."""
    local = reference.astimezone(IST)
    day_start_ist = local.replace(hour=0, minute=0, second=0, microsecond=0)
    day_end_ist = day_start_ist + timedelta(days=1)
    return day_start_ist.astimezone(pytz.utc), day_end_ist.astimezone(pytz.utc)


def _ist_month_bounds(reference: datetime) -> tuple[datetime, datetime]:
    """Return (start_of_month, start_of_next_month) in UTC for the IST month containing `reference`."""
    local = reference.astimezone(IST)
    month_start_ist = local.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    # roll to first day of next month
    if month_start_ist.month == 12:
        next_month_ist = month_start_ist.replace(year=month_start_ist.year + 1, month=1)
    else:
        next_month_ist = month_start_ist.replace(month=month_start_ist.month + 1)
    return month_start_ist.astimezone(pytz.utc), next_month_ist.astimezone(pytz.utc)


def _ist_prev_month_bounds(reference: datetime) -> tuple[datetime, datetime]:
    """Return bounds for the month before the one containing `reference`."""
    local = reference.astimezone(IST)
    this_month_start = local.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    if this_month_start.month == 1:
        prev_month_start = this_month_start.replace(year=this_month_start.year - 1, month=12)
    else:
        prev_month_start = this_month_start.replace(month=this_month_start.month - 1)
    return _ist_month_bounds(prev_month_start.astimezone(pytz.utc))


def _fmt_amount(amount: Any) -> str:
    try:
        return f"₹{Decimal(str(amount)):,.2f}"
    except Exception:
        return f"₹{amount}"


def _bar(fraction: float, width: int = 10) -> str:
    filled = round(fraction * width)
    return "█" * filled + "░" * (width - filled)


async def build_daily_report() -> str:
    now_utc = datetime.now(pytz.utc)
    day_start, day_end = _ist_day_bounds(now_utc)
    data = await get_daily_report(day_start, day_end)

    local_date = now_utc.astimezone(IST).strftime("%d %b %Y")

    if not data["breakdown"]:
        return f"📅 *Daily Report — {local_date}*\n\nNo expenses logged today."

    total = data["total"]
    lines = [f"📅 *Daily Report — {local_date}*", f"*Total: {_fmt_amount(total)}*", ""]

    for item in data["breakdown"]:
        cat_total = item["total"]
        frac = float(cat_total) / float(total) if total else 0
        pct = frac * 100
        lines.append(
            f"{_bar(frac)} {item['category']}\n"
            f"   {_fmt_amount(cat_total)}  ({pct:.1f}%)"
        )

    return "\n".join(lines)


async def build_monthly_report() -> str:
    now_utc = datetime.now(pytz.utc)
    month_start, month_end = _ist_month_bounds(now_utc)
    data = await get_monthly_report(month_start, month_end)

    prev_start, prev_end = _ist_prev_month_bounds(now_utc)
    prev_total = await get_monthly_report_last_month(prev_start, prev_end)

    local_month = now_utc.astimezone(IST).strftime("%B %Y")

    if not data["breakdown"]:
        return f"🗓 *Monthly Report — {local_month}*\n\nNo expenses logged this month."

    total = data["total"]

    # comparison arrow
    if prev_total and prev_total > 0:
        diff_pct = (float(total) - float(prev_total)) / float(prev_total) * 100
        if diff_pct > 0:
            cmp = f"▲ {diff_pct:.1f}% vs last month ({_fmt_amount(prev_total)})"
        elif diff_pct < 0:
            cmp = f"▼ {abs(diff_pct):.1f}% vs last month ({_fmt_amount(prev_total)})"
        else:
            cmp = f"Same as last month ({_fmt_amount(prev_total)})"
    else:
        cmp = "No data for last month"

    lines = [
        f"🗓 *Monthly Report — {local_month}*",
        f"*Total: {_fmt_amount(total)}*",
        f"_{cmp}_",
        "",
    ]

    for item in data["breakdown"]:
        cat_total = item["total"]
        frac = float(cat_total) / float(total) if total else 0
        pct = frac * 100
        lines.append(
            f"{_bar(frac)} {item['category']}\n"
            f"   {_fmt_amount(cat_total)}  ({pct:.1f}%)"
        )

    return "\n".join(lines)
