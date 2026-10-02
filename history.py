from __future__ import annotations

import json
import logging
from dataclasses import asdict
from datetime import date, datetime
from pathlib import Path

from config import (
    CATEGORY_BRANDS,
    CATEGORY_DISAMBIGUATION_KEYWORDS,
    BRAND_FIELD_CATEGORY_OVERRIDES,
    PG_BRANDS,
    PRIVATE_LABEL_BRANDS,
    MISSING_DAYS_THRESHOLD,
)

logger = logging.getLogger(__name__)

DATA_DIR = Path("data")


def _norm(s: str) -> str:
    return (s or "").strip().casefold()


def _contains_as_word(haystack: str, needle: str) -> bool:
    """Whole-word substring match, case-insensitive."""
    import re

    pattern = r"(?<![a-zA-Z0-9])" + re.escape(needle) + r"(?![a-zA-Z0-9])"
    return re.search(pattern, haystack, flags=re.IGNORECASE) is not None


def _build_watchlist_index():
    """
    Build a lookup from normalized brand name -> category, using
    CATEGORY_BRANDS (the curated "fighting category" taxonomy) plus any
    per-brand category overrides.
    """
    index = {}
    for category, brands in CATEGORY_BRANDS.items():
        for brand in brands.get("pg_brands", []) + brands.get("competitor_brands", []):
            index[_norm(brand)] = category
    for brand, category in BRAND_FIELD_CATEGORY_OVERRIDES.items():
        index[_norm(brand)] = category
    return index


_WATCHLIST_INDEX = _build_watchlist_index()


def match_pg_brand(brand_field: str, product_field: str = "") -> str | None:
    """
    Return the canonical P&G brand name if brand_field (or, failing that,
    product_field) matches one of PG_BRANDS, else None.
    """
    norm_brand = _norm(brand_field)
    for pg_brand in PG_BRANDS:
        if _norm(pg_brand) == norm_brand or _contains_as_word(norm_brand, _norm(pg_brand)):
            return pg_brand
    if product_field:
        norm_product = _norm(product_field)
        for pg_brand in PG_BRANDS:
            if _contains_as_word(norm_product, _norm(pg_brand)):
                return pg_brand
    return None


def is_pg(brand_field: str, product_field: str = "") -> bool:
    return match_pg_brand(brand_field, product_field) is not None


def _keyword_rule_matches(product_name: str, category: str) -> bool:
    keywords = CATEGORY_DISAMBIGUATION_KEYWORDS.get(category, [])
    norm_product = _norm(product_name)
    return any(_contains_as_word(norm_product, _norm(kw)) for kw in keywords)


def retailer_dir(retailer: str) -> Path:
    d = DATA_DIR / "history" / retailer
    d.mkdir(parents=True, exist_ok=True)
    return d


def day_path(retailer: str, day: date) -> Path:
    return retailer_dir(retailer) / f"{day.isoformat()}.json"


def build_day_payload(retailer: str, day: date, stores: list) -> dict:
    """
    Reduce a full list of Store objects (with nested Product items) for one
    day into a compact payload: just the P&G-relevant rows we need for
    history/flagging, plus a couple of summary counts.
    """
    pg_products = []
    for store in stores:
        for item in store.items:
            brand = getattr(item, "brand", "") or ""
            product_name = getattr(item, "product", "") or ""
            if not is_pg(brand, product_name):
                continue
            pg_products.append(
                {
                    "store_id": store.store_id,
                    "store_name": store.name,
                    "city": getattr(store, "city", ""),
                    "product_id": getattr(item, "product_id", ""),
                    "product": product_name,
                    "brand": brand,
                    "category": getattr(item, "category", "") or "",
                    "price": getattr(item, "price", None),
                }
            )

    return {
        "retailer": retailer,
        "date": day.isoformat(),
        "stores_count": len(stores),
        "pg_product_rows": len(pg_products),
        "pg_products": pg_products,
    }


def save_day(retailer: str, day: date, payload: dict) -> None:
    path = day_path(retailer, day)
    with path.open("w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False)


def load_day(retailer: str, day: date) -> dict | None:
    path = day_path(retailer, day)
    if not path.exists():
        return None
    try:
        with path.open("r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError) as exc:
        logger.warning("Could not read %s: %s", path, exc)
        return None


def available_dates(retailer: str) -> list[date]:
    d = retailer_dir(retailer)
    dates = []
    for p in d.glob("*.json"):
        try:
            dates.append(date.fromisoformat(p.stem))
        except ValueError:
            continue
    return sorted(dates)


def build_product_price_history(retailer: str, lookback_days: int = 90) -> dict:
    """
    Walk all saved history files for `retailer` and build, per product_id,
    a time series of {date, price} points (deduplicated/averaged across
    stores for a simple national trend line).
    """
    dates = available_dates(retailer)
    dates = dates[-lookback_days:]

    # product_id -> {"product": str, "brand": str, "points": {date_str: [prices]}}
    series: dict[str, dict] = {}

    for d in dates:
        payload = load_day(retailer, d)
        if not payload:
            continue
        for row in payload["pg_products"]:
            pid = row["product_id"]
            entry = series.setdefault(
                pid,
                {"product": row["product"], "brand": row["brand"], "points": {}},
            )
            if row.get("price") is not None:
                entry["points"].setdefault(d.isoformat(), []).append(row["price"])

    history = {}
    for pid, entry in series.items():
        points = [
            {"date": d_str, "price": round(sum(prices) / len(prices), 2)}
            for d_str, prices in sorted(entry["points"].items())
        ]
        if points:
            history[pid] = {
                "product": entry["product"],
                "brand": entry["brand"],
                "points": points,
            }

    return history


def compute_missing_flags(
    retailer: str, as_of: date, threshold: int = MISSING_DAYS_THRESHOLD, lookback_days: int = 60
) -> list[dict]:
    """
    Walk backwards from `as_of` through saved history files for `retailer`
    and flag any (store_id, product_id) that:
      - has appeared at least once in the retained history, and
      - is absent from the price list for `threshold`+ consecutive
        *published* days counting back from the most recent file we have,
      never counting back further than the product's first-ever appearance.

    This is an inference from public price-list presence/absence, not real
    warehouse inventory data.
    """
    dates = [d for d in available_dates(retailer) if d <= as_of]
    dates = dates[-lookback_days:]
    if not dates:
        return []

    # product_key -> {"first_seen": date, "last_seen": date, "meta": {...}}
    seen: dict[tuple, dict] = {}
    presence_by_date: dict[date, set] = {}

    for d in dates:
        payload = load_day(retailer, d)
        if not payload:
            continue
        present_today = set()
        for row in payload["pg_products"]:
            key = (row["store_id"], row["product_id"])
            present_today.add(key)
            info = seen.setdefault(key, {"first_seen": d, "meta": row})
            info["last_seen_present"] = d
            info["meta"] = row  # keep freshest metadata
        presence_by_date[d] = present_today

    flags = []
    most_recent = dates[-1]
    for key, info in seen.items():
        # Count consecutive missing days ending at most_recent, not going
        # back further than first_seen. Walking backwards (most recent
        # first), `first_missing_date` keeps getting overwritten with an
        # earlier date as long as the item is still missing, so by the time
        # the loop stops it holds the OLDEST date in this missing streak —
        # i.e. the first day the gap started, not just how many days long it is.
        missing_days = 0
        first_missing_date = None
        for d in reversed(dates):
            if d < info["first_seen"]:
                break
            if key in presence_by_date.get(d, set()):
                break
            missing_days += 1
            first_missing_date = d
        if missing_days >= threshold:
            meta = info["meta"]
            flags.append(
                {
                    "store_id": meta["store_id"],
                    "store_name": meta["store_name"],
                    "city": meta.get("city", ""),
                    "product_id": meta["product_id"],
                    "product": meta["product"],
                    "brand": meta["brand"],
                    "category": meta["category"],
                    "last_seen_price": meta.get("price"),
                    # The last published day this exact (store, product) was
                    # actually present, tracked as we walk forward through
                    # `dates` above — not just "how many days missing", so a
                    # manager can tell a product that vanished last week from
                    # one that's been gone since the start of the window.
                    "last_seen_date": info["last_seen_present"].isoformat(),
                    # Together with last_seen_date, this bounds the actual gap:
                    # present through last_seen_date, absent from
                    # first_missing_date through as_of (most_recent) below.
                    "first_missing_date": first_missing_date.isoformat() if first_missing_date else None,
                    "days_missing": missing_days,
                    "as_of": most_recent.isoformat(),
                }
            )

    flags.sort(key=lambda f: f["days_missing"], reverse=True)
    return flags