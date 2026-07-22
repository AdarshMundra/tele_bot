"""Core message handling logic — shared by webhook (main.py) and polling (local_dev.py)."""
from __future__ import annotations

import logging
from typing import Any

import httpx

from app.config import settings
from app.db import (
    delete_expense,
    get_expense_by_id,
    get_last_expense,
    insert_expense,
)
from app.llm import extract_expense
from app.reports import build_daily_report, build_monthly_report

logger = logging.getLogger(__name__)

TELEGRAM_API = f"https://api.telegram.org/bot{settings.telegram_bot_token}"


# ---------------------------------------------------------------------------
# Telegram API helper
# ---------------------------------------------------------------------------

async def send_message(chat_id: int, text: str, parse_mode: str = "Markdown") -> None:
    async with httpx.AsyncClient() as client:
        resp = await client.post(
            f"{TELEGRAM_API}/sendMessage",
            json={"chat_id": chat_id, "text": text, "parse_mode": parse_mode},
            timeout=10,
        )
    if resp.status_code != 200:
        logger.error("sendMessage failed: %s", resp.text)


# ---------------------------------------------------------------------------
# Command handlers
# ---------------------------------------------------------------------------

async def cmd_start(chat_id: int) -> None:
    text = (
        "👋 *Expense Tracker Bot*\n\n"
        "Just send me a message like:\n"
        "  • `coffee 120`\n"
        "  • `uber to airport 450 yesterday`\n"
        "  • `groceries 890`\n\n"
        "*Commands:*\n"
        "/daily — today's spending\n"
        "/monthly — this month's spending\n"
        "/undo — delete last logged expense\n"
        "/delete `<id>` — delete expense by ID\n"
        "/check — ping server and wake it up if sleeping\n"
        "/help — show this message"
    )
    await send_message(chat_id, text)


async def cmd_help(chat_id: int) -> None:
    await cmd_start(chat_id)


async def cmd_daily(chat_id: int) -> None:
    report = await build_daily_report()
    await send_message(chat_id, report)


async def cmd_monthly(chat_id: int) -> None:
    report = await build_monthly_report()
    await send_message(chat_id, report)


async def cmd_undo(chat_id: int) -> None:
    row = await get_last_expense()
    if not row:
        await send_message(chat_id, "No expenses to undo.")
        return
    deleted = await delete_expense(row["id"])
    if deleted:
        await send_message(
            chat_id,
            f"🗑 Deleted: *{row['description']}* — ₹{row['amount']:.2f} (id {row['id']})",
        )
    else:
        await send_message(chat_id, "Could not delete the expense. It may already be gone.")


async def cmd_check(chat_id: int) -> None:
    """Ping the Render /health endpoint to confirm the server is up (and wake it if sleeping)."""
    url = settings.render_service_url
    if not url:
        await send_message(chat_id, "⚠️ `RENDER_SERVICE_URL` is not configured.")
        return

    health_url = url.rstrip("/") + "/health"
    await send_message(chat_id, f"Pinging `{health_url}` …")
    try:
        import time
        t0 = time.monotonic()
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.get(health_url)
        elapsed = time.monotonic() - t0

        if resp.status_code == 200:
            await send_message(
                chat_id,
                f"✅ Server is *up* and healthy.\n⏱ Response time: {elapsed:.2f}s",
            )
        else:
            await send_message(
                chat_id,
                f"⚠️ Server responded with HTTP {resp.status_code} in {elapsed:.2f}s",
            )
    except httpx.TimeoutException:
        await send_message(chat_id, "⏳ Request timed out (30 s). Server may still be waking up — try again in a moment.")
    except Exception as exc:
        logger.exception("cmd_check failed")
        await send_message(chat_id, f"❌ Could not reach server: `{exc}`")


async def cmd_delete(chat_id: int, args: str) -> None:
    parts = args.strip().split()
    if not parts or not parts[0].isdigit():
        await send_message(chat_id, "Usage: /delete `<id>`\nExample: `/delete 42`")
        return
    expense_id = int(parts[0])
    row = await get_expense_by_id(expense_id)
    if not row:
        await send_message(chat_id, f"No expense found with id {expense_id}.")
        return
    deleted = await delete_expense(expense_id)
    if deleted:
        await send_message(
            chat_id,
            f"🗑 Deleted: *{row['description']}* — ₹{row['amount']:.2f} (id {expense_id})",
        )
    else:
        await send_message(chat_id, "Could not delete the expense.")


# ---------------------------------------------------------------------------
# Expense extraction (the "hot path")
# ---------------------------------------------------------------------------

async def handle_expense_text(chat_id: int, text: str) -> None:
    """Extract, validate, store, and confirm an expense from free text."""
    try:
        extraction, occurred_at = await extract_expense(text)
    except Exception as e:
        logger.exception("LLM extraction failed for %r", text)
        await send_message(
            chat_id,
            f"⚠️ Couldn't parse that as an expense.\n_{e}_\n\nTry: `coffee 120`",
        )
        return

    try:
        expense_id = await insert_expense(
            amount=extraction.amount,
            description=extraction.description,
            category=extraction.category,
            subcategory=extraction.subcategory,
            occurred_at=occurred_at,
            raw_message=text,
        )
    except Exception:
        logger.exception("DB insert failed")
        await send_message(chat_id, "⚠️ Failed to save the expense. Please try again.")
        return

    date_str = occurred_at.astimezone(__import__("pytz").timezone("Asia/Kolkata")).strftime(
        "%d %b %Y"
    )
    await send_message(
        chat_id,
        f"✅ *{extraction.description}*\n"
        f"₹{extraction.amount:,.2f}  •  {extraction.category} → {extraction.subcategory}\n"
        f"📅 {date_str}  •  id `{expense_id}`",
    )


# ---------------------------------------------------------------------------
# Update router
# ---------------------------------------------------------------------------

async def handle_update(update: dict[str, Any]) -> None:
    """Route a Telegram update dict to the correct handler."""
    message = update.get("message") or update.get("edited_message")
    if not message:
        return

    sender_id = message.get("from", {}).get("id")
    if sender_id != settings.allowed_telegram_user_id:
        logger.debug("Ignored update from sender %s", sender_id)
        return

    chat_id: int = message["chat"]["id"]
    text: str = message.get("text", "").strip()

    if not text:
        return

    if text.startswith("/"):
        # split command and args
        parts = text.split(None, 1)
        command = parts[0].split("@")[0].lower()  # strip @botname suffix
        args = parts[1] if len(parts) > 1 else ""

        if command == "/start":
            await cmd_start(chat_id)
        elif command == "/help":
            await cmd_help(chat_id)
        elif command == "/daily":
            await cmd_daily(chat_id)
        elif command == "/monthly":
            await cmd_monthly(chat_id)
        elif command == "/undo":
            await cmd_undo(chat_id)
        elif command == "/delete":
            await cmd_delete(chat_id, args)
        elif command == "/check":
            await cmd_check(chat_id)
        else:
            await send_message(chat_id, f"Unknown command: `{command}`\nType /help for usage.")
    else:
        await handle_expense_text(chat_id, text)
