# Telegram Expense Tracker Bot — Project Brief

## Goal
A single-user Telegram bot that logs expenses from natural-language messages. When the
user sends something like "coffee 120" or "uber to airport 450 yesterday", an LLM extracts
the amount, description, category, and subcategory, then stores it in a database. The user
can request on-demand daily and monthly reports via commands.

## Tech stack
- **Backend:** Python + FastAPI (async), served with uvicorn/gunicorn
- **Hosting:** Koyeb (public URL, request-based)
- **LLM:** OpenAI API (structured/JSON output). Use a cheap nano-tier model for
  extraction + classification — no reasoning model needed. Confirm the exact current
  model string against OpenAI docs at build time, since model names change.
- **Database:** Supabase (Postgres), accessed via a direct Postgres connection string
  (asyncpg / SQLAlchemy preferred over the REST client for aggregation queries)
- **Telegram:** webhook-based (NOT polling), since we run on Koyeb with a public URL

## Locked decisions
- **Single-user.** No `users` table. One allowlisted Telegram user ID in env; silently
  ignore every other sender at the webhook.
- **Reports: on-demand only.** No scheduler, no cron, no background jobs. Just `/daily`
  and `/monthly` command handlers that query and reply.
- **No receipt photos.** Text-only for now.
- **Currency: INR only.** No currency field, no conversion. All timezone/date math in
  `Asia/Kolkata` (IST).

---

## Architecture

```
Telegram  ->  Koyeb (FastAPI webhook)  ->  OpenAI (extract + categorize)
                      |
                      v
                Supabase (Postgres)
                      ^
      /daily, /monthly commands -> query -> format reply
```

Flow: user message -> Telegram POSTs to `/webhook/{secret}` -> verify secret + allowlist
-> if command, route to handler; if free text, call OpenAI -> validate JSON with Pydantic
-> insert into `expenses` -> reply with confirmation.

**Reply-timing note:** Telegram retries if the webhook doesn't respond within a few
seconds. If the OpenAI call is slow, ack the update immediately (return 200) and send the
parsed result as a separate `sendMessage` call, rather than blocking the webhook response.

---

## Database schema (Supabase / Postgres)

```sql
create table expenses (
  id            bigserial primary key,
  amount        numeric(12,2) not null,   -- rupees; never use float for money
  description   text not null,
  category      text not null,
  subcategory   text not null,
  occurred_at   timestamptz not null,     -- when the expense happened (IST-resolved)
  created_at    timestamptz not null default now(),
  raw_message   text                      -- original Telegram text, for re-parse/debug
);

create index idx_expenses_occurred_at on expenses (occurred_at);
```

- Use `numeric`, never floats, for money.
- `occurred_at` and `created_at` are separate on purpose: "logged today about yesterday's
  coffee" must report on the correct day.
- Keep `raw_message` so a mis-categorized entry can be re-run later.

---

## LLM contract

Send the user's text to OpenAI and require a **strict JSON object** (use structured
outputs / JSON schema — do not accept prose). Validate with a Pydantic model before any
DB write.

Output schema per expense:
```json
{
  "amount": 0,
  "description": "string, cleaned merchant/item",
  "category": "string, must be from the fixed taxonomy",
  "subcategory": "string, must be from the fixed taxonomy",
  "occurred_at": "ISO date if the user gave a relative/explicit date, else null"
}
```

Resolution rules (done in Python, NOT by the model):
- If `occurred_at` is null, default to now in IST.
- If the model returns a date (from "yesterday", "on the 3rd", etc.), interpret it in IST.
- Never trust the model to do date math — it only reports what it saw; Python resolves the
  actual timestamp.

### Fixed taxonomy (closed list — model must pick from this)
Constrain category/subcategory to an enum in the JSON schema so the model can't invent
categories. Always include the fallback so there's a valid landing spot.

- **Food & Dining** -> Groceries, Restaurants, Cafes, Delivery, Snacks
- **Transport** -> Fuel, Public transit, Ride-hailing, Parking, Tolls
- **Housing & Utilities** -> Rent, Electricity, Water, Internet, Gas, Maintenance
- **Shopping** -> Clothing, Electronics, Household, Personal care
- **Health** -> Pharmacy, Doctor, Insurance, Fitness
- **Entertainment** -> Streaming, Movies, Games, Events, Hobbies
- **Finance** -> Fees, Interest, Transfers, Investments
- **Travel** -> Flights, Hotels, Local transport
- **Miscellaneous** -> Gifts, Donations, Other   <-- guaranteed fallback

---

## Commands
- `/start` — welcome + short usage help
- `/help` — usage help
- `/daily` — today's total + per-category breakdown (IST day boundaries)
- `/monthly` — current calendar month total + per-category breakdown (IST)
- `/undo` — delete the most recently logged expense
- `/delete` — delete a specific expense (by id)
- `/edit` — edit a specific expense (nice-to-have, later)
- Any non-command text = "log an expense"

### Report query notes
- **Daily:** sum + group-by-category where `occurred_at` falls within the user's local
  (IST) day. Include a total and a top-few breakdown.
- **Monthly:** same for the calendar month, plus per-category breakdown and optionally a
  comparison to last month.
- Do all day/month boundary math in IST, not UTC, or expenses land on the wrong day.

---

## Security
- Verify Telegram's `X-Telegram-Bot-Api-Secret-Token` header (set when registering the
  webhook) so nobody can POST fake updates.
- Enforce `ALLOWED_TELEGRAM_USER_ID`: reject/ignore any message from a different sender.
  Without this, a stranger who finds the bot could run up the OpenAI bill and pollute the DB.

---

## Environment variables / Koyeb secrets
```
TELEGRAM_BOT_TOKEN
TELEGRAM_WEBHOOK_SECRET
OPENAI_API_KEY
SUPABASE_DB_URL            # direct Postgres connection string
ALLOWED_TELEGRAM_USER_ID   # your numeric Telegram user ID
TIMEZONE=Asia/Kolkata      # or hardcode it
```
Store everything as Koyeb secrets — never in code.

---

## Deployment (Koyeb)
- Dockerfile or Koyeb buildpack for a Python/FastAPI app.
- Bind to the port Koyeb assigns via `$PORT`; run with uvicorn/gunicorn.
- Add a `/health` endpoint for Koyeb health checks.
- After deploy, register the webhook ONCE by calling Telegram's `setWebhook` with the
  Koyeb public URL + secret path + secret token.
- Cold starts: if the service scales to zero, the first update after idle may be slow.
  Fine for personal use; set a min-instance if it becomes annoying.

---

## Build order
1. FastAPI skeleton + `/health` -> deploy to Koyeb, confirm reachable.
2. Telegram webhook: receive updates, verify secret header + allowlist, echo reply
   (proves the loop end-to-end).
3. Supabase `expenses` table + DB connection wired up.
4. **Core:** LLM extract -> Pydantic validate -> insert -> confirmation reply. Get this solid.
5. `/daily` and `/monthly` on-demand reports with IST boundaries.
6. Polish: `/undo`, `/delete`, `/edit`, nicer confirmation formatting.

---

## Local dev tips
- Polling (`getUpdates`) is fine for local testing; webhooks are for prod on Koyeb.
- Always validate the model's JSON output before insert — even a good schema can return
  edge cases.
- Log `raw_message` from day one; it's invaluable for debugging categorization.