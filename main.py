"""
FastAPI webhook application — production mode (Koyeb).

Flow:
  Telegram POSTs to /webhook/{secret}
  → verify secret header + allowlist (in handler)
  → ack with 200 immediately
  → process update in background task (avoids Telegram retry on slow OpenAI calls)
"""
from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from typing import Any

from fastapi import BackgroundTasks, FastAPI, Header, HTTPException, Request, status

from app.config import settings
from app.db import close_pool, init_pool
from app.handlers import handle_update

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s — %(message)s",
)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("Initialising DB pool…")
    await init_pool()
    logger.info("DB pool ready.")
    yield
    logger.info("Shutting down DB pool…")
    await close_pool()


app = FastAPI(title="Expense Tracker Bot", lifespan=lifespan)


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/webhook/{secret}")
async def webhook(
    secret: str,
    request: Request,
    background_tasks: BackgroundTasks,
    x_telegram_bot_api_secret_token: str = Header(default=""),
) -> dict[str, str]:
    # 1. Verify path secret matches env
    if secret != settings.telegram_webhook_secret:
        logger.warning("Path secret mismatch: got %d chars, expected %d chars", len(secret), len(settings.telegram_webhook_secret))
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Bad secret")

    # 2. Verify Telegram header secret (set when registering the webhook)
    if x_telegram_bot_api_secret_token != settings.telegram_webhook_secret:
        logger.warning("Header secret mismatch: got %r (len=%d), expected len=%d", x_telegram_bot_api_secret_token[:4] + "***", len(x_telegram_bot_api_secret_token), len(settings.telegram_webhook_secret))
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Bad header secret")

    update: dict[str, Any] = await request.json()
    logger.debug("Received update id=%s", update.get("update_id"))

    # 3. Ack immediately; process in background so Telegram doesn't retry
    background_tasks.add_task(handle_update, update)
    return {"ok": "true"}
