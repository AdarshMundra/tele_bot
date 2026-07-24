from __future__ import annotations

from decimal import Decimal
from typing import Optional

from pydantic import BaseModel, field_validator

# ---------------------------------------------------------------------------
# Fixed expense taxonomy
# ---------------------------------------------------------------------------

TAXONOMY: dict[str, list[str]] = {
    "Food & Dining": ["Groceries", "Restaurants", "Cafes", "Delivery", "Snacks"],
    "Transport": ["Fuel", "Public transit", "Ride-hailing", "Parking", "Tolls"],
    "Housing & Utilities": ["Rent", "Electricity", "Water", "Internet", "Gas", "Maintenance"],
    "Shopping": ["Clothing", "Electronics", "Household", "Personal care"],
    "Health": ["Pharmacy", "Doctor", "Insurance", "Fitness"],
    "Entertainment": ["Streaming", "Movies", "Games", "Events", "Hobbies"],
    "Finance": ["Fees", "Interest", "Transfers", "Investments"],
    "Travel": ["Flights", "Hotels", "Local transport"],
    "Miscellaneous": ["Gifts", "Donations", "Other"],
}

ALL_CATEGORIES: list[str] = list(TAXONOMY.keys())
ALL_SUBCATEGORIES: list[str] = [sub for subs in TAXONOMY.values() for sub in subs]


def valid_combination(category: str, subcategory: str) -> bool:
    return subcategory in TAXONOMY.get(category, [])


# ---------------------------------------------------------------------------
# OpenAI structured output schema (used as JSON schema in the API call)
# ---------------------------------------------------------------------------

EXPENSE_JSON_SCHEMA = {
    "type": "json_schema",
    "json_schema": {
        "name": "expense_extraction",
        "strict": True,
        "schema": {
            "type": "object",
            "properties": {
                "amount": {
                    "type": "number",
                    "description": "Numeric amount in INR, no currency symbol",
                },
                "description": {
                    "type": "string",
                    "description": "Cleaned merchant or item name",
                },
                "category": {
                    "type": "string",
                    "enum": ALL_CATEGORIES,
                    "description": "Top-level category from the fixed taxonomy",
                },
                "subcategory": {
                    "type": "string",
                    "enum": ALL_SUBCATEGORIES,
                    "description": "Subcategory from the fixed taxonomy, must belong to the chosen category",
                },
                "occurred_at": {
                    "type": ["string", "null"],
                    "description": (
                        "If the user mentioned a specific or relative date "
                        "(e.g. 'yesterday', 'on the 3rd'), return the resolved "
                        "date as YYYY-MM-DD. Otherwise return null."
                    ),
                },
                "payment_mode": {
                    "type": "string",
                    "enum": ["upi", "cash", "card", "netbanking", "wallet"],
                    "default": "upi",
                    "description": (
                        "How the payment was made. 'upi' for UPI/GPay/PhonePe/Paytm transfers, "
                        "'cash' for physical cash, 'card' for swipe/tap on a card terminal, "
                        "'netbanking' for net banking, 'wallet' for e-wallets. "
                        "Null if not mentioned."
                    ),
                },
                "payment_source": {
                    "type": "string",
                    "enum": ["credit card", "debit card", "HDFC", "IOB account", "BOB account"],
                    "default": "credit card",
                    "description": (
                        "The account or card debited, e.g. 'credit card', 'debit card', "
                        "'HDFC savings', 'SBI account'. Null if not mentioned."
                    ),
                },
            },
            "required": ["amount", "description", "category", "subcategory", "occurred_at", "payment_mode", "payment_source"],
            "additionalProperties": False,
        },
    },
}


# ---------------------------------------------------------------------------
# Pydantic model for validating LLM output before DB write
# ---------------------------------------------------------------------------

class ExpenseExtraction(BaseModel):
    amount: Decimal
    description: str
    category: str
    subcategory: str
    occurred_at: Optional[str] = None  # YYYY-MM-DD or null
    payment_mode: Optional[str] = "UPI"  # upi, cash, card, netbanking, wallet
    payment_source: Optional[str] = "Credit Card"  # credit card, debit card, HDFC savings, etc.

    @field_validator("amount")
    @classmethod
    def amount_positive(cls, v: Decimal) -> Decimal:
        if v <= 0:
            raise ValueError("amount must be positive")
        return v

    @field_validator("category")
    @classmethod
    def category_valid(cls, v: str) -> str:
        if v not in ALL_CATEGORIES:
            raise ValueError(f"Unknown category: {v}")
        return v

    @field_validator("subcategory")
    @classmethod
    def subcategory_valid(cls, v: str) -> str:
        if v not in ALL_SUBCATEGORIES:
            raise ValueError(f"Unknown subcategory: {v}")
        return v

    def validate_taxonomy_combination(self) -> None:
        if not valid_combination(self.category, self.subcategory):
            raise ValueError(
                f"Subcategory '{self.subcategory}' does not belong to '{self.category}'"
            )
