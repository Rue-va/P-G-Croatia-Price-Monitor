"""
Lightweight JSON-file history store, kept small enough to live in git.

Design choice: rather than a binary SQLite file (which git can't diff and
which grows without bound), each retailer/day gets one small JSON file
containing only:
  - aggregate stats for the WHOLE assortment (so "total catalog size" and
    "P&G vs. average price per category" can be shown without keeping every
    competitor SKU forever), and
  - the full row-level detail for P&G products only (since that's the part
    we need per-store, per-day presence history for the missing-item flag).

This keeps each file's size proportional to P&G's assortment, not the
retailer's whole catalog, so the repo stays small even after a year of daily
history.
"""
from __future__ import annotations

import json
import re
from collections import Counter
from dataclasses import dataclass, asdict
from datetime import date, timedelta
from pathlib import Path
from statistics import mean
from typing import Iterable

from config import HISTORY_DIR, MISSING_DAYS_THRESHOLD, PG_BRANDS

# Sorted longest-first so "HEAD & SHOULDERS" is tried before any shorter
# brand name that might also appear as a substring of it.
_PG_BRANDS_BY_LENGTH = sorted(PG_BRANDS, key=len, reverse=True)


def _norm(text: str) -> str:
    """Upper-case and collapse whitespace, for tolerant string matching."""
    return re.sub(r"\s+", " ", (text or "").strip().upper())


def match_pg_brand(brand: str, product_name: str = "") -> tuple[bool, str]:
    """
    Decide whether a row is a P&G product, and how we decided.

    Some retailers (Lidl's CSVs are the known case so far) leave the
    dedicated brand column blank or use free-text that doesn't match our
    brand list, while still naming the brand inside the product title
    itself (e.g. brand="" but product="Pampers Pants Giant Pack vel. 4").
    Matching on brand alone silently drops those rows to zero, which is
    exactly the bug seen on Lidl's first run. So: try the brand field first
    (cheap, precise), and if that comes up empty, fall back to a substring
    search over the product name.

    Returns (is_pg, matched_via) where matched_via is "brand",
    "product_name", or "" if no match.
    """
    norm_brand = _norm(brand)
    if norm_brand in PG_BRANDS:
        return True, "brand"

    norm_product = _norm(product_name)
    if norm_product:
        for pg_brand in _PG_BRANDS_BY_LENGTH:
            if pg_brand in norm_product:
                return True, "product_name"

    return False, ""


def is_pg(brand: str) -> bool:
    """Back-compat wrapper: brand-field-only check."""
    return _norm(brand) in PG_BRANDS


def retailer_dir(retailer: str) -> Path:
    d = HISTORY_DIR / retailer
    d.mkdir(parents=True, exist_ok=True)
    return d


def day_path(retailer: str, day: date) -> Path:
    return retailer_dir(retailer) / f"{day.isoformat()}.json"


def build_day_payload(retailer: str, day: date, stores: list) -> dict:
    """
    stores: list of crawler_vendor.models.Store objects (already crawled).
    """
    category_stats: dict[str, dict] = {}
    pg_rows: list[dict] = []
    total_products = 0
    stores_count = len(stores)
    blank_brand_count = 0
    all_brand_counter: Counter = Counter()
    match_via_counter: Counter = Counter()

    for store in stores:
        for item in store.items:
            total_products += 1
            category = (item.category or "Uncategorized").strip() or "Uncategorized"
            price = float(item.price) if item.price is not None else None
            norm_brand = _norm(item.brand)
            if norm_brand:
                all_brand_counter[norm_brand] += 1
            else:
                blank_brand_count += 1

            cs = category_stats.setdefault(
                category,
                {
                    "count": 0, "prices": [],
                    "pg_count": 0, "pg_prices": [],
                    "competitor_prices": [],
                },
            )
            cs["count"] += 1
            if price is not None:
                cs["prices"].append(price)

            matched, via = match_pg_brand(item.brand, item.product)
            if matched:
                match_via_counter[via] += 1
                cs["pg_count"] += 1
                if price is not None:
                    cs["pg_prices"].append(price)
                pg_rows.append(
                    {
                        "store_id": store.store_id,
                        "store_name": store.name,
                        "city": store.city,
                        "product_id": item.product_id,
                        "product": item.product,
                        "brand": item.brand,
                        "category": category,
                        "price": price,
                        "barcode": item.barcode,
                        "matched_via": via,
                    }
                )
            elif price is not None:
                # Non-P&G row with a usable price: this is the true
                # "competitor" population for price comparisons, kept
                # separate from the all-brands average (which otherwise
                # gets diluted by P&G's own prices).
                cs["competitor_prices"].append(price)

    # collapse raw price lists into summary numbers before saving
    for cat, cs in category_stats.items():
        prices = cs.pop("prices")
        pg_prices = cs.pop("pg_prices")
        competitor_prices = cs.pop("competitor_prices")
        cs["avg_price"] = round(mean(prices), 2) if prices else None
        cs["pg_avg_price"] = round(mean(pg_prices), 2) if pg_prices else None
        cs["competitor_avg_price"] = round(mean(competitor_prices), 2) if competitor_prices else None
        cs["competitor_count"] = len(competitor_prices)

    return {
        "date": day.isoformat(),
        "retailer": retailer,
        "stores_count": stores_count,
        "total_products_all_stores": total_products,
        "pg_product_rows": len(pg_rows),
        "category_stats": category_stats,
        "pg_products": pg_rows,
        "diagnostics": {
            "blank_brand_field_rows": blank_brand_count,
            "blank_brand_field_pct": round(100 * blank_brand_count / total_products, 1) if total_products else 0,
            "matched_via_brand_field": match_via_counter.get("brand", 0),
            "matched_via_product_name_fallback": match_via_counter.get("product_name", 0),
            "top_brands_seen": all_brand_counter.most_common(40),
        },
    }


def save_day(retailer: str, day: date, payload: dict) -> Path:
    path = day_path(retailer, day)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")
    return path


def load_day(retailer: str, day: date) -> dict | None:
    path = day_path(retailer, day)
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def available_dates(retailer: str) -> list[date]:
    d = retailer_dir(retailer)
    out = []
    for f in d.glob("*.json"):
        try:
            out.append(date.fromisoformat(f.stem))
        except ValueError:
            continue
    return sorted(out)


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
        # back further than first_seen.
        missing_days = 0
        for d in reversed(dates):
            if d < info["first_seen"]:
                break
            if key in presence_by_date.get(d, set()):
                break
            missing_days += 1
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
                    "days_missing": missing_days,
                    "as_of": most_recent.isoformat(),
                }
            )

    flags.sort(key=lambda f: f["days_missing"], reverse=True)
    return flags