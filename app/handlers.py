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
    update_expense_field,
    EDITABLE_FIELDS,
)
from app.llm import extract_expense
from app.reports import build_daily_report, build_monthly_report, build_weekly_report, build_summary

logger = logging.getLogger(__name__)

TELEGRAM_API = f"https://api.telegram.org/bot{settings.telegram_bot_token}"

# Persistent reply keyboard shown after /start and /help
REPLY_KEYBOARD = {
    "keyboard": [
        [{"text": "📊 Daily"}, {"text": "📅 Weekly"}],
        [{"text": "🗓 Monthly"}, {"text": "📋 Summary"}],
    ],
    "resize_keyboard": True,
    "persistent": True,
}

# Map button labels to commands
KEYBOARD_BUTTON_MAP = {
    "📊 Daily": "daily",
    "📅 Weekly": "weekly",
    "🗓 Monthly": "monthly",
    "📋 Summary": "summary",
}


# ---------------------------------------------------------------------------
# Telegram API helpers
# ---------------------------------------------------------------------------

async def send_message(chat_id: int, text: str, parse_mode: str = "Markdown", reply_markup: dict | None = None) -> None:
    payload: dict[str, Any] = {"chat_id": chat_id, "text": text, "parse_mode": parse_mode}
    if reply_markup is not None:
        payload["reply_markup"] = reply_markup
    async with httpx.AsyncClient() as client:
        resp = await client.post(f"{TELEGRAM_API}/sendMessage", json=payload, timeout=10)
    if resp.status_code != 200:
        logger.error("sendMessage failed: %s", resp.text)


async def answer_callback_query(callback_query_id: str, text: str = "") -> None:
    async with httpx.AsyncClient() as client:
        await client.post(
            f"{TELEGRAM_API}/answerCallbackQuery",
            json={"callback_query_id": callback_query_id, "text": text},
            timeout=10,
        )


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
        "/weekly — this week's spending\n"
        "/monthly — this month's spending\n"
        "/summary — today / week / month at a glance\n"
        "/undo — delete last logged expense\n"
        "/edit `<id> <field> <value>` — edit an expense field\n"
        "/delete `<id>` — delete expense by ID\n"
        "/start — ping server and show this message\n"
        "/help — show this message"
    )
    await send_message(chat_id, text, reply_markup=REPLY_KEYBOARD)
    await cmd_check(chat_id)


async def cmd_help(chat_id: int) -> None:
    await cmd_start(chat_id)


async def cmd_daily(chat_id: int) -> None:
    report = await build_daily_report()
    await send_message(chat_id, report)


async def cmd_monthly(chat_id: int) -> None:
    report = await build_monthly_report()
    await send_message(chat_id, report)


async def cmd_weekly(chat_id: int) -> None:
    report = await build_weekly_report()
    await send_message(chat_id, report)


async def cmd_summary(chat_id: int) -> None:
    report = await build_summary()
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


async def cmd_edit(chat_id: int, args: str) -> None:
    """Edit a field of an expense. Usage: /edit <id> <field> <value>"""
    parts = args.strip().split(None, 2)
    if len(parts) < 3 or not parts[0].isdigit():
        fields = ", ".join(f"`{f}`" for f in sorted(EDITABLE_FIELDS))
        await send_message(
            chat_id,
            f"Usage: `/edit <id> <field> <value>`\n\nEditable fields: {fields}\n\nExamples:\n"
            "`/edit 42 description lunch with team`\n"
            "`/edit 42 amount 350`\n"
            "`/edit 42 category Food`",
        )
        return

    expense_id = int(parts[0])
    field = parts[1].lower()
    value = parts[2].strip()

    if field not in EDITABLE_FIELDS:
        fields = ", ".join(f"`{f}`" for f in sorted(EDITABLE_FIELDS))
        await send_message(chat_id, f"Unknown field `{field}`. Editable fields: {fields}")
        return

    row = await get_expense_by_id(expense_id)
    if not row:
        await send_message(chat_id, f"No expense found with id {expense_id}.")
        return

    old_value = str(row[field]) if row[field] is not None else "—"

    try:
        updated = await update_expense_field(expense_id, field, value)
    except Exception as e:
        await send_message(chat_id, f"⚠️ Could not update expense: `{e}`")
        return

    if updated:
        await send_message(
            chat_id,
            f"✏️ Updated expense `{expense_id}`\n*{field}*: {old_value} → {value}",
        )
    else:
        await send_message(chat_id, f"Could not update expense {expense_id}. It may not exist.")


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
            payment_mode=extraction.payment_mode,
            payment_source=extraction.payment_source,
        )
    except Exception:
        logger.exception("DB insert failed")
        await send_message(chat_id, "⚠️ Failed to save the expense. Please try again.")
        return

    date_str = occurred_at.astimezone(__import__("pytz").timezone("Asia/Kolkata")).strftime(
        "%d %b %Y"
    )
    payment_parts = []
    if extraction.payment_mode:
        payment_parts.append(extraction.payment_mode.upper())
    if extraction.payment_source:
        payment_parts.append(extraction.payment_source)
    payment_str = f"  •  💳 {' / '.join(payment_parts)}" if payment_parts else ""

    inline_kb = {
        "inline_keyboard": [[
            {"text": "🗑 Undo", "callback_data": f"undo_{expense_id}"},
            {"text": "✏️ Edit", "callback_data": f"edit_{expense_id}"},
        ]]
    }
    await send_message(
        chat_id,
        f"✅ *{extraction.description}*\n"
        f"₹{extraction.amount:,.2f}  •  {extraction.category} → {extraction.subcategory}\n"
        f"📅 {date_str}{payment_str}  •  id `{expense_id}`",
        reply_markup=inline_kb,
    )


# ---------------------------------------------------------------------------
# Callback query handler (inline button presses)
# ---------------------------------------------------------------------------

async def handle_callback_query(callback_query: dict[str, Any]) -> None:
    cq_id = callback_query["id"]
    sender_id = callback_query.get("from", {}).get("id")
    chat_id: int = callback_query["message"]["chat"]["id"]
    data: str = callback_query.get("data", "")

    if sender_id != settings.allowed_telegram_user_id:
        await answer_callback_query(cq_id, "Unauthorized.")
        return

    if data.startswith("undo_"):
        expense_id = int(data.split("_", 1)[1])
        row = await get_expense_by_id(expense_id)
        if not row:
            await answer_callback_query(cq_id, "Already deleted.")
            return
        deleted = await delete_expense(expense_id)
        if deleted:
            await answer_callback_query(cq_id, "Deleted!")
            await send_message(
                chat_id,
                f"🗑 Deleted: *{row['description']}* — ₹{row['amount']:.2f} (id {expense_id})",
            )
        else:
            await answer_callback_query(cq_id, "Could not delete.")

    elif data.startswith("edit_"):
        expense_id = int(data.split("_", 1)[1])
        await answer_callback_query(cq_id)
        fields = ", ".join(f"`{f}`" for f in sorted(EDITABLE_FIELDS))
        await send_message(
            chat_id,
            f"To edit expense `{expense_id}`, send:\n"
            f"`/edit {expense_id} <field> <value>`\n\n"
            f"Editable fields: {fields}\n\n"
            f"Example: `/edit {expense_id} amount 350`",
        )
    else:
        await answer_callback_query(cq_id)


# ---------------------------------------------------------------------------
# Update router
# ---------------------------------------------------------------------------

async def handle_update(update: dict[str, Any]) -> None:
    """Route a Telegram update dict to the correct handler."""

    # Handle inline button presses
    if "callback_query" in update:
        await handle_callback_query(update["callback_query"])
        return

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

    # Handle reply keyboard button presses (they send plain text labels)
    if text in KEYBOARD_BUTTON_MAP:
        text = "/" + KEYBOARD_BUTTON_MAP[text]

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
        elif command == "/weekly":
            await cmd_weekly(chat_id)
        elif command == "/monthly":
            await cmd_monthly(chat_id)
        elif command == "/summary":
            await cmd_summary(chat_id)
        elif command == "/undo":
            await cmd_undo(chat_id)
        elif command == "/edit":
            await cmd_edit(chat_id, args)
        elif command == "/delete":
            await cmd_delete(chat_id, args)
        else:
            await send_message(chat_id, f"Unknown command: `{command}`\nType /help for usage.")
    else:
        await handle_expense_text(chat_id, text)
