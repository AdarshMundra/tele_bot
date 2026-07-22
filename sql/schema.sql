-- Run this once in your Supabase SQL editor (or psql)
-- to create the expenses table.

create table if not exists expenses (
  id            bigserial primary key,
  amount        numeric(12,2) not null,   -- rupees; never use float for money
  description   text not null,
  category      text not null,
  subcategory   text not null,
  occurred_at   timestamptz not null,     -- when the expense happened (IST-resolved, stored as UTC)
  created_at    timestamptz not null default now(),
  raw_message   text                      -- original Telegram text, for re-parse/debug
);

create index if not exists idx_expenses_occurred_at on expenses (occurred_at);
