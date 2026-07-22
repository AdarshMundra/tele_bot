"""
One-time script to register the Telegram webhook with Koyeb public URL.

Usage (after deploying to Koyeb):
    KOYEB_URL=https://your-app.koyeb.app python setup_webhook.py

Or set KOYEB_URL in your .env and just run:
    python setup_webhook.py
"""
from __future__ import annotations

import os
import sys

import httpx
from dotenv import load_dotenv

load_dotenv()

BOT_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
WEBHOOK_SECRET = os.environ["TELEGRAM_WEBHOOK_SECRET"]
KOYEB_URL = os.environ.get("KOYEB_URL", "").rstrip("/")

if not KOYEB_URL:
    print("ERROR: Set KOYEB_URL environment variable to your Koyeb public URL.")
    print("Example: KOYEB_URL=https://your-app.koyeb.app python setup_webhook.py")
    sys.exit(1)

webhook_url = f"{KOYEB_URL}/webhook/{WEBHOOK_SECRET}"
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
import json
print(json.dumps(info_resp.json(), indent=2))
