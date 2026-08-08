"""OpenAI integration — extract and categorize expenses from free text."""
from __future__ import annotations

import json
import logging
from datetime import date, datetime
from typing import Optional

import pytz
from openai import AsyncOpenAI

from app.config import settings
from app.models import EXPENSE_JSON_SCHEMA, ExpenseExtraction

logger = logging.getLogger(__name__)

_client: Optional[AsyncOpenAI] = None
IST = pytz.timezone(settings.timezone)


def get_client() -> AsyncOpenAI:
    global _client
    if _client is None:
        _client = AsyncOpenAI(api_key=settings.openai_api_key)
    return _client


SYSTEM_PROMPT = """You are an expense-parsing assistant for a personal finance tracker.

The user sends informal messages in English (or a mix of English and Hindi) describing \
money they spent. Extract the expense details and return them as a JSON object.

Rules:
- amount: numeric value in INR, never negative
- description: cleaned, concise merchant or item name (e.g. "Starbucks coffee", "Uber to airport")
- category and subcategory: must be picked EXACTLY from the provided taxonomy — do not invent new ones
- occurred_at: if the user mentions a date (e.g. "yesterday", "on the 3rd", "last Monday"), \
resolve it to YYYY-MM-DD using today's date provided below. If no date is mentioned, return null.
- payment_mode: how the payment was made — one of: "upi", "cash", "card", "netbanking", "wallet". \
"upi" covers UPI, GPay, PhonePe, Paytm transfers AND any bank account mention (e.g. "IOB account", "BOB account", "HDFC account"). \
"card" means a credit or debit card was explicitly swiped/tapped. \
Return "upi" if not mentioned.
- payment_source: what account or card was debited — e.g. "credit card", "debit card", \
"HDFC savings", "IOB account", "BOB account". Return "credit card" if not mentioned. \
Examples: "IOB account" → payment_mode=upi, payment_source=IOB account; \
"icici credit card" → payment_mode=card, payment_source=ICICI credit card; \
"cash" → payment_mode=cash, payment_source=null; \
"gpay" → payment_mode=upi, payment_source=null.

Today's date (IST): {today}

Taxonomy:
Food & Dining → Groceries, Restaurants, Cafes, Delivery, Snacks
Transport → Fuel, Public transit, Ride-hailing, Parking, Tolls
Housing & Utilities → Rent, Electricity, Water, Internet, Gas, Maintenance
Shopping → Clothing, Electronics, Household, Personal care, Online, Other
Health → Pharmacy, Doctor, Insurance, Fitness
Entertainment → Streaming, Movies, Games, Events, Hobbies
Finance → Fees, Interest, Transfers, Investments
Travel → Flights, Hotels, Local transport
Miscellaneous → Gifts, Donations, Other
"""


def _build_system_prompt() -> str:
    today_ist = datetime.now(IST).date().isoformat()
    return SYSTEM_PROMPT.format(today=today_ist)


def _resolve_occurred_at(date_str: Optional[str]) -> datetime:
    """Convert YYYY-MM-DD string to an aware UTC datetime (noon IST). Falls back to now."""
    now_ist = datetime.now(IST)
    if not date_str:
        return now_ist.astimezone(pytz.utc)
    try:
        parsed: date = date.fromisoformat(date_str)
        # Use noon IST so day boundaries never drift
        naive_noon = datetime(parsed.year, parsed.month, parsed.day, 12, 0, 0)
        return IST.localize(naive_noon).astimezone(pytz.utc)
    except (ValueError, TypeError):
        logger.warning("Could not parse occurred_at %r, using now", date_str)
        return now_ist.astimezone(pytz.utc)


async def extract_expense(text: str) -> tuple[ExpenseExtraction, datetime]:
    """
    Call OpenAI to extract expense fields from free text.

    Returns:
        (ExpenseExtraction, occurred_at as UTC-aware datetime)
    Raises:
        ValueError if the response cannot be validated.
    """
    response = await get_client().chat.completions.create(
        model=settings.openai_model,
        messages=[
            {"role": "system", "content": _build_system_prompt()},
            {"role": "user", "content": text},
        ],
        response_format=EXPENSE_JSON_SCHEMA,
        temperature=0,
    )

    raw_json = response.choices[0].message.content
    logger.debug("LLM raw response: %s", raw_json)

    data = json.loads(raw_json)
    extraction = ExpenseExtraction(**data)
    extraction.validate_taxonomy_combination()

    occurred_at = _resolve_occurred_at(extraction.occurred_at)
    return extraction, occurred_at
