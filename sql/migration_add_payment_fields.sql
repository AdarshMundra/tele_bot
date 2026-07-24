-- Run this in your Supabase SQL editor to add payment fields to the existing expenses table.

ALTER TABLE expenses
  ADD COLUMN IF NOT EXISTS payment_mode   text,   -- upi, cash, card, netbanking, wallet
  ADD COLUMN IF NOT EXISTS payment_source text;   -- credit card, debit card, HDFC savings, etc.
