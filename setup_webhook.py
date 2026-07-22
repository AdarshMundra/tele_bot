"""
One-time script to register the Telegram webhook after deploying to Render.

Usage:
    RENDER_URL=https://your-app.onrender.com python setup_webhook.py

Or add RENDER_URL to your .env and just run:
    python setup_webhook.py
"""
from __future__ import annotations

import json
import os
import sys

import httpx
from dotenv import load_dotenv

load_dotenv()

BOT_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
WEBHOOK_SECRET = os.environ["TELEGRAM_WEBHOOK_SECRET"]
RENDER_URL = os.environ.get("RENDER_URL", "").rstrip("/")

if not RENDER_URL:
    print("ERROR: Set RENDER_URL to your Render public URL.")
    print("Example: RENDER_URL=https://your-app.onrender.com python setup_webhook.py")
    sys.exit(1)

webhook_url = f"{RENDER_URL}/webhook/{WEBHOOK_SECRET}"
print(f"Registering webhook: {webhook_url}")

resp = httpx.post(
    f"https://api.telegram.org/bot{BOT_TOKEN}/setWebhook",
    json={
        "url": webhook_url,
        "secret_token": WEBHOOK_SECRET,
        "allowed_updates": ["message"],
        "drop_pending_updates": True,
    },
)
data = resp.json()

if data.get("ok"):
    print("✅ Webhook registered successfully.")
    print(f"   Telegram will POST updates to: {webhook_url}")
else:
    print(f"❌ Failed to register webhook: {data}")
    sys.exit(1)

# Verify
info_resp = httpx.get(f"https://api.telegram.org/bot{BOT_TOKEN}/getWebhookInfo")
print("\nWebhook info:")
print(json.dumps(info_resp.json(), indent=2))
