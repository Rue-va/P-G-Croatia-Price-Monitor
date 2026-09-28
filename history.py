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
from dataclasses import dataclass, asdict
from datetime import date, timedelta
from pathlib import Path
from statistics import mean
from typing import Iterable

from config import HISTORY_DIR, MISSING_DAYS_THRESHOLD, PG_BRANDS


def retailer_dir(retailer: str) -> Path:
    d = HISTORY_DIR / retailer
    d.mkdir(parents=True, exist_ok=True)
    return d


def day_path(retailer: str, day: date) -> Path:
    return retailer_dir(retailer) / f"{day.isoformat()}.json"


def is_pg(brand: str) -> bool:
    return (brand or "").strip().upper() in PG_BRANDS


def build_day_payload(retailer: str, day: date, stores: list) -> dict:
    """
    stores: list of crawler_vendor.models.Store objects (already crawled).
    """
    category_stats: dict[str, dict] = {}
    pg_rows: list[dict] = []
    total_products = 0
    stores_count = len(stores)

    for store in stores:
        for item in store.items:
            total_products += 1
            category = (item.category or "Uncategorized").strip() or "Uncategorized"
            price = float(item.price) if item.price is not None else None

            cs = category_stats.setdefault(
                category, {"count": 0, "prices": [], "pg_count": 0, "pg_prices": []}
            )
            cs["count"] += 1
            if price is not None:
                cs["prices"].append(price)

            if is_pg(item.brand):
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
                    }
                )

    # collapse raw price lists into summary numbers before saving
    for cat, cs in category_stats.items():
        prices = cs.pop("prices")
        pg_prices = cs.pop("pg_prices")
        cs["avg_price"] = round(mean(prices), 2) if prices else None
        cs["pg_avg_price"] = round(mean(pg_prices), 2) if pg_prices else None

    return {
        "date": day.isoformat(),
        "retailer": retailer,
        "stores_count": stores_count,
        "total_products_all_stores": total_products,
        "pg_product_rows": len(pg_rows),
        "category_stats": category_stats,
        "pg_products": pg_rows,
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
