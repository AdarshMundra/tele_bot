"""
Local development runner — uses long-polling (getUpdates) instead of webhooks.

Usage:
    python local_dev.py

No public URL needed. The same handler logic from app/handlers.py is reused.
Make sure .env is populated before running.
"""
from __future__ import annotations

import asyncio
import logging
import sys

import httpx

from app.config import settings
from app.db import close_pool, init_pool
from app.handlers import handle_update

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s — %(message)s",
)
logger = logging.getLogger(__name__)

TELEGRAM_API = f"https://api.telegram.org/bot{settings.telegram_bot_token}"
POLL_TIMEOUT = 30  # seconds for long-polling


async def delete_webhook() -> None:
    """Remove any existing webhook so polling works."""
    async with httpx.AsyncClient() as client:
        resp = await client.post(f"{TELEGRAM_API}/deleteWebhook", json={"drop_pending_updates": False})
    data = resp.json()
    if data.get("ok"):
        logger.info("Webhook cleared (polling mode active).")
    else:
        logger.warning("deleteWebhook response: %s", data)


async def poll_loop() -> None:
    offset: int = 0
    logger.info("Starting long-poll loop…  (Ctrl-C to stop)")

    async with httpx.AsyncClient(timeout=POLL_TIMEOUT + 5) as client:
        while True:
            try:
                resp = await client.get(
                    f"{TELEGRAM_API}/getUpdates",
                    params={"offset": offset, "timeout": POLL_TIMEOUT, "allowed_updates": ["message"]},
                )
                data = resp.json()
                if not data.get("ok"):
                    logger.error("getUpdates error: %s", data)
                    await asyncio.sleep(2)
                    continue

                for update in data.get("result", []):
                    offset = update["update_id"] + 1
                    try:
                        await handle_update(update)
                    except Exception:
                        logger.exception("Error handling update %s", update.get("update_id"))

            except httpx.ReadTimeout:
                # Normal for long-polling — just loop again
                continue
            except Exception:
                logger.exception("Unexpected polling error, retrying in 3s…")
                await asyncio.sleep(3)


async def main() -> None:
    logger.info("Initialising DB pool…")
    await init_pool()
    logger.info("DB pool ready.")

    await delete_webhook()

    try:
        await poll_loop()
    except (KeyboardInterrupt, asyncio.CancelledError):
        logger.info("Stopping…")
    finally:
        await close_pool()
        logger.info("Shutdown complete.")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        sys.exit(0)
