"""Build daily and monthly report strings and compute IST date boundaries."""
from __future__ import annotations

from datetime import datetime, timedelta
from decimal import Decimal
from typing import Any

import pytz

from app.config import settings
from app.db import (
    get_daily_report,
    get_daily_totals_for_week,
    get_monthly_report,
    get_monthly_report_last_month,
    get_payment_breakdown,
    get_subcategory_breakdown,
    get_weekly_report,
)

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


def _ist_week_bounds(reference: datetime) -> tuple[datetime, datetime]:
    """Return (monday_start_utc, next_monday_utc) for the ISO week containing `reference`."""
    local = reference.astimezone(IST)
    monday_ist = local.replace(hour=0, minute=0, second=0, microsecond=0) - timedelta(days=local.weekday())
    next_monday_ist = monday_ist + timedelta(weeks=1)
    return monday_ist.astimezone(pytz.utc), next_monday_ist.astimezone(pytz.utc)


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

    subcats = await get_subcategory_breakdown(month_start, month_end)
    cat_subcats: dict[str, list[dict]] = {}
    for s in subcats:
        cat_subcats.setdefault(s["category"], []).append(s)

    for item in data["breakdown"]:
        cat_total = item["total"]
        frac = float(cat_total) / float(total) if total else 0
        pct = frac * 100
        lines.append(
            f"{_bar(frac)} {item['category']}\n"
            f"   {_fmt_amount(cat_total)}  ({pct:.1f}%)"
        )
        subs = cat_subcats.get(item["category"], [])
        if subs:
            parts = "  •  ".join(f"{s['subcategory']} {_fmt_amount(s['total'])}" for s in subs)
            lines.append(f"   └ {parts}")

    payment = await get_payment_breakdown(month_start, month_end)
    if payment["by_mode"] or payment["by_source"]:
        lines.append("")
        lines.append("*💳 Payment:*")
        if payment["by_mode"]:
            lines.append("  •  ".join(f"{m['mode']} {_fmt_amount(m['total'])}" for m in payment["by_mode"]))
        if payment["by_source"]:
            lines.append("  •  ".join(f"{s['source']} {_fmt_amount(s['total'])}" for s in payment["by_source"]))

    return "\n".join(lines)


async def build_summary() -> str:
    now_utc = datetime.now(pytz.utc)
    now_ist = now_utc.astimezone(IST)

    day_start, day_end = _ist_day_bounds(now_utc)
    week_start, week_end = _ist_week_bounds(now_utc)
    month_start, month_end = _ist_month_bounds(now_utc)

    day_data = await get_daily_report(day_start, day_end)
    week_data = await get_weekly_report(week_start, week_end)
    month_data = await get_monthly_report(month_start, month_end)

    days_elapsed = max(1, now_ist.day)
    daily_avg = Decimal(str(float(month_data["total"]) / days_elapsed)) if month_data["total"] else Decimal("0")

    lines = [
        "📊 *Summary*",
        "",
        f"Today:       {_fmt_amount(day_data['total'])}",
        f"This week:   {_fmt_amount(week_data['total'])}",
        f"This month:  {_fmt_amount(month_data['total'])}",
        "",
        f"Daily avg (month): {_fmt_amount(daily_avg)}",
    ]
    return "\n".join(lines)


async def build_weekly_report() -> str:
    now_utc = datetime.now(pytz.utc)
    week_start, week_end = _ist_week_bounds(now_utc)

    data = await get_weekly_report(week_start, week_end)
    daily_rows = await get_daily_totals_for_week(week_start, week_end)
    payment = await get_payment_breakdown(week_start, week_end)
    subcats = await get_subcategory_breakdown(week_start, week_end)

    monday_ist = week_start.astimezone(IST)
    sunday_ist = (week_end - timedelta(seconds=1)).astimezone(IST)
    week_label = f"{monday_ist.day}–{sunday_ist.day} {sunday_ist.strftime('%b %Y')}"

    total = data["total"]

    daily_map: dict = {}
    for r in daily_rows:
        daily_map[r["day"]] = r["total"]

    day_names = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
    day_totals = []
    for i in range(7):
        d = (monday_ist + timedelta(days=i)).date()
        day_totals.append((d, daily_map.get(d, Decimal("0"))))

    max_day = max((t for _, t in day_totals), default=Decimal("1")) or Decimal("1")

    lines = [
        f"📆 *Weekly Report — {week_label}*",
        f"*Total: {_fmt_amount(total)}*",
        "",
        "*Daily:*",
    ]
    for i, (d, dt) in enumerate(day_totals):
        frac = float(dt) / float(max_day) if max_day else 0
        day_label = f"{day_names[i]} {d.day}"
        if dt:
            lines.append(f"{day_label:<7} {_bar(frac)}  {_fmt_amount(dt)}")
        else:
            lines.append(f"{day_label:<7} {'░' * 10}  ₹0")

    if data["breakdown"]:
        lines.append("")
        lines.append("*By category:*")

        cat_subcats: dict[str, list[dict]] = {}
        for s in subcats:
            cat_subcats.setdefault(s["category"], []).append(s)

        for item in data["breakdown"]:
            cat_total = item["total"]
            frac = float(cat_total) / float(total) if total else 0
            pct = frac * 100
            lines.append(f"{_bar(frac)}  {item['category']:<20} {_fmt_amount(cat_total)}  ({pct:.1f}%)")
            subs = cat_subcats.get(item["category"], [])
            if subs:
                parts = "  •  ".join(f"{s['subcategory']} {_fmt_amount(s['total'])}" for s in subs)
                lines.append(f"   └ {parts}")

    if payment["by_mode"] or payment["by_source"]:
        lines.append("")
        lines.append("*💳 Payment:*")
        if payment["by_mode"]:
            lines.append("  •  ".join(f"{m['mode']} {_fmt_amount(m['total'])}" for m in payment["by_mode"]))
        if payment["by_source"]:
            lines.append("  •  ".join(f"{s['source']} {_fmt_amount(s['total'])}" for s in payment["by_source"]))

    return "\n".join(lines)
