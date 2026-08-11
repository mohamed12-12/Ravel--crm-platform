from __future__ import annotations

import json
from typing import Any


ROOM_PRICE_TYPES = ("Single", "Double", "Triple")
ROOM_PRICE_CURRENCIES = ("EGP", "USD")


def _normalize_room_type(value: Any) -> str:
    normalized = str(value or "").strip().title()
    return normalized if normalized in ROOM_PRICE_TYPES else ""


def _normalize_currency(value: Any) -> str:
    normalized = str(value or "").strip().upper()
    return normalized if normalized in ROOM_PRICE_CURRENCIES else ""


def parse_room_prices(value: Any) -> dict[str, dict[str, str]]:
    raw = value
    if isinstance(raw, str):
        text = raw.strip()
        if not text:
            raw = {}
        else:
            try:
                raw = json.loads(text)
            except json.JSONDecodeError:
                raw = {}
    if not isinstance(raw, dict):
        return {}

    normalized: dict[str, dict[str, str]] = {}
    for room_type, currencies in raw.items():
        canonical_room = _normalize_room_type(room_type)
        if not canonical_room or not isinstance(currencies, dict):
            continue
        room_prices: dict[str, str] = {}
        for currency, price in currencies.items():
            canonical_currency = _normalize_currency(currency)
            price_text = str(price or "").strip()
            if canonical_currency and price_text:
                room_prices[canonical_currency] = price_text
        if room_prices:
            normalized[canonical_room] = room_prices
    return normalized


def room_prices_grid(value: Any) -> dict[str, dict[str, str]]:
    parsed = parse_room_prices(value)
    return {
        room_type: {
            currency: str(parsed.get(room_type, {}).get(currency) or "").strip()
            for currency in ROOM_PRICE_CURRENCIES
        }
        for room_type in ROOM_PRICE_TYPES
    }


def serialize_room_prices(value: Any) -> str:
    parsed = parse_room_prices(value)
    if not parsed:
        return ""
    return json.dumps(parsed, ensure_ascii=False, sort_keys=True)


def room_prices_from_form(data: dict[str, Any]) -> dict[str, dict[str, str]]:
    prices: dict[str, dict[str, str]] = {}
    for room_type in ROOM_PRICE_TYPES:
        room_key = room_type.lower()
        room_prices: dict[str, str] = {}
        for currency in ROOM_PRICE_CURRENCIES:
            key = f"{room_key}_price_{currency.lower()}"
            price = str(data.get(key) or "").strip()
            if price:
                room_prices[currency] = price
        if room_prices:
            prices[room_type] = room_prices
    return prices


def has_room_price_inputs(data: dict[str, Any]) -> bool:
    return any(
        key in data
        for key in (
            f"{room_type.lower()}_price_{currency.lower()}"
            for room_type in ROOM_PRICE_TYPES
            for currency in ROOM_PRICE_CURRENCIES
        )
    )


def price_for_room_and_currency(
    trip: dict[str, Any] | Any,
    *,
    room_type: str = "",
    currency: str = "",
    fallback_public_price: bool = True,
) -> str:
    trip_dict = trip if isinstance(trip, dict) else {}
    room_prices = parse_room_prices(
        trip_dict.get("room_prices")
        or trip_dict.get("room_prices_json")
        or getattr(trip, "room_prices_json", "")
    )
    canonical_room = _normalize_room_type(room_type)
    canonical_currency = _normalize_currency(currency)
    if canonical_room and canonical_currency:
        price = str(room_prices.get(canonical_room, {}).get(canonical_currency) or "").strip()
        if price:
            return price
    if fallback_public_price:
        return str(
            (trip_dict.get("public_price") if isinstance(trip_dict, dict) else "") or getattr(trip, "public_price", "") or ""
        ).strip()
    return ""
