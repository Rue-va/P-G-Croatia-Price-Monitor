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

from config import (
    BRAND_FIELD_CATEGORY_OVERRIDES,
    CATEGORY_BRANDS,
    CATEGORY_DISAMBIGUATION_KEYWORDS,
    HISTORY_DIR,
    LOOKBACK_DAYS,
    MISSING_DAYS_THRESHOLD,
    PG_BRANDS,
    PRIVATE_LABEL_BRANDS,
)

# Sorted longest-first so "HEAD & SHOULDERS" is tried before any shorter
# brand name that might also appear as a substring of it.
_PG_BRANDS_BY_LENGTH = sorted(PG_BRANDS, key=len, reverse=True)

_word_match_cache: dict[str, re.Pattern] = {}


def _contains_as_word(haystack: str, needle: str) -> bool:
    """
    True if `needle` appears in `haystack` as a whole word, not merely as a
    substring. Needed because some watchlist brand names are very short
    ("Fa", "BIC") and a plain substring check false-matches them inside
    unrelated words — e.g. "BIC" inside "CUBICS" (a bag of chips) or "FA"
    inside "FARCHIONI" (an olive oil brand), silently miscategorizing
    completely unrelated products into Shave Care / APDO. \\b uses Python's
    Unicode-aware \\w, so it also respects Croatian diacritics (č, š, ž, đ).
    """
    pattern = _word_match_cache.get(needle)
    if pattern is None:
        pattern = re.compile(r"\b" + re.escape(needle) + r"\b")
        _word_match_cache[needle] = pattern
    return bool(pattern.search(haystack))


def _keyword_rule_matches(norm_product: str, rule) -> bool:
    """
    Evaluate one CATEGORY_DISAMBIGUATION_KEYWORDS rule against a normalized
    product name. `rule` is either a plain list of keywords (matches if ANY
    are present — the original, simple form) or a dict with "include"
    and/or "exclude" keyword lists (matches if an "include" keyword is
    present AND no "exclude" keyword is present). The dict form is for
    brands that sell several genuinely different product lines under
    overlapping wording, e.g. Violeta's "Baby Wipes" vs. its makeup-removal
    wipes and wet toilet paper, which all share the Croatian word
    "maramice".
    """
    if isinstance(rule, dict):
        include = rule.get("include", [])
        exclude = rule.get("exclude", [])
        if exclude and any(kw in norm_product for kw in exclude):
            return False
        return any(kw in norm_product for kw in include) if include else True
    return any(kw in norm_product for kw in rule)


def _build_watchlist_index(retailer: str):
    """
    Builds the per-retailer lookup structures for CATEGORY_BRANDS:
      - brand_to_categories: normalized brand -> list of category names it
        appears in (usually one; Violeta/Jar appear in two)
      - brand_display: normalized brand -> the nicely-cased name from config
      - brand_lookup_by_length: normalized brands sorted longest-first, for
        the product-name substring fallback
      - init_prices: category -> normalized brand -> [] (price accumulator)
    """
    brand_to_categories: dict[str, list[str]] = {}
    brand_display: dict[str, str] = {}
    init_prices: dict[str, dict[str, list]] = {}

    for category, brands in CATEGORY_BRANDS.items():
        brands = list(brands)
        private_label = PRIVATE_LABEL_BRANDS.get(retailer)
        if category == "Baby Wipes" and private_label and private_label not in brands:
            brands = brands + [private_label]

        init_prices[category] = {}
        for brand in brands:
            norm = _norm(brand)
            brand_to_categories.setdefault(norm, []).append(category)
            brand_display[norm] = brand
            init_prices[category][norm] = []

    brand_lookup_by_length = sorted(brand_to_categories, key=len, reverse=True)
    return brand_to_categories, brand_display, brand_lookup_by_length, init_prices


def _norm(text: str) -> str:
    """
    Upper-case, treat hyphens as spaces, and collapse whitespace, for
    tolerant string matching. The hyphen/space fold matters because a
    canonical brand name in config.py (e.g. "Oral-B") and a retailer's own
    brand column (e.g. Konzum's "ORAL B", no hyphen) can spell the same
    brand differently — without this, the watchlist match silently fails
    and the brand shows as "not found today" even when it's clearly in the
    data.
    """
    return re.sub(r"\s+", " ", (text or "").replace("-", " ").strip().upper())


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
            if _contains_as_word(norm_product, pg_brand):
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
    # Row-level detail for competitor brands on the manager's watchlist only
    # (config.CATEGORY_BRANDS) — a bounded, small set (~50 brands), unlike the
    # full competitor catalog, which is why this is safe to keep per-row while
    # the rest of the competitor catalog stays aggregate-only. This is what
    # lets the dashboard drill into "which stores, what price" when someone
    # clicks a competitor brand chip in the Competitor Tracker.
    watchlist_competitor_rows: list[dict] = []
    total_products = 0
    stores_count = len(stores)
    blank_brand_count = 0
    all_brand_counter: Counter = Counter()
    match_via_counter: Counter = Counter()

    watch_brand_to_categories, watch_brand_display, watch_brand_by_length, watch_prices = (
        _build_watchlist_index(retailer)
    )

    for store in stores:
        for item in store.items:
            total_products += 1
            category = (item.category or "Uncategorized").strip() or "Uncategorized"
            price = float(item.price) if item.price is not None else None
            norm_brand = _norm(item.brand)
            norm_product = _norm(item.product)
            if norm_brand:
                all_brand_counter[norm_brand] += 1
            else:
                blank_brand_count += 1

            row_fighting_category = None
            if price is not None:
                override = BRAND_FIELD_CATEGORY_OVERRIDES.get(norm_brand)
                if override:
                    override_brand_display, target_cat = override
                    override_norm_brand = _norm(override_brand_display)
                    if override_norm_brand in watch_prices.get(target_cat, {}):
                        watch_prices[target_cat][override_norm_brand].append(price)
                        row_fighting_category = target_cat
                else:
                    matched_watch_brand = norm_brand if norm_brand in watch_brand_to_categories else None
                    if matched_watch_brand is None and norm_product:
                        for wb in watch_brand_by_length:
                            if _contains_as_word(norm_product, wb):
                                matched_watch_brand = wb
                                break
                    if matched_watch_brand:
                        cats_for_brand = watch_brand_to_categories[matched_watch_brand]
                        if len(cats_for_brand) == 1:
                            target_cat = cats_for_brand[0]
                        else:
                            target_cat = None
                            for cat in cats_for_brand:
                                rule = CATEGORY_DISAMBIGUATION_KEYWORDS.get(matched_watch_brand, {}).get(cat, [])
                                if _keyword_rule_matches(norm_product, rule):
                                    target_cat = cat
                                    break
                        if target_cat:
                            watch_prices[target_cat][matched_watch_brand].append(price)
                            row_fighting_category = target_cat

            cs = category_stats.setdefault(
                category,
                {
                    "count": 0, "prices": [],
                    "pg_count": 0, "pg_prices": [],
                    "competitor_prices": [], "competitor_brands": {},
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
                        "street_address": store.street_address,
                        "city": store.city,
                        "product_id": item.product_id,
                        "product": item.product,
                        "brand": item.brand,
                        "category": category,
                        "fighting_category": row_fighting_category,
                        "price": price,
                        "barcode": item.barcode,
                        "matched_via": via,
                    }
                )
            elif price is not None:
                cs["competitor_prices"].append(price)
                if norm_brand:
                    slot = cs["competitor_brands"].setdefault(
                        norm_brand, {"display": item.brand.strip(), "prices": []}
                    )
                    slot["prices"].append(price)
                if row_fighting_category:
                    watchlist_competitor_rows.append(
                        {
                            "store_id": store.store_id,
                            "store_name": store.name,
                            "street_address": store.street_address,
                            "city": store.city,
                            "product_id": item.product_id,
                            "product": item.product,
                            "brand": item.brand,
                            "fighting_category": row_fighting_category,
                            "price": price,
                            "barcode": item.barcode,
                        }
                    )

    for cat, cs in category_stats.items():
        prices = cs.pop("prices")
        pg_prices = cs.pop("pg_prices")
        competitor_prices = cs.pop("competitor_prices")
        competitor_brands = cs.pop("competitor_brands")
        cs["avg_price"] = round(mean(prices), 2) if prices else None
        cs["pg_avg_price"] = round(mean(pg_prices), 2) if pg_prices else None
        cs["competitor_avg_price"] = round(mean(competitor_prices), 2) if competitor_prices else None
        cs["competitor_count"] = len(competitor_prices)

        top_brands = sorted(
            competitor_brands.values(), key=lambda b: len(b["prices"]), reverse=True
        )[:8]
        cs["top_competitor_brands"] = [
            {
                "brand": b["display"],
                "count": len(b["prices"]),
                "avg_price": round(mean(b["prices"]), 2),
            }
            for b in top_brands
        ]

    category_brand_watchlist: dict[str, dict] = {}
    for cat, brands in watch_prices.items():
        pg_brands, competitor_brands_out = [], []
        for norm, prices in brands.items():
            display = watch_brand_display[norm]
            entry = {
                "brand": display,
                "count": len(prices),
                "avg_price": round(mean(prices), 2) if prices else None,
            }
            (pg_brands if is_pg(display) else competitor_brands_out).append(entry)
        category_brand_watchlist[cat] = {
            "pg_brands": pg_brands,
            "competitor_brands": competitor_brands_out,
        }

    return {
        "date": day.isoformat(),
        "retailer": retailer,
        "stores_count": stores_count,
        "total_products_all_stores": total_products,
        "pg_product_rows": len(pg_rows),
        "category_stats": category_stats,
        "category_brand_watchlist": category_brand_watchlist,
        "pg_products": pg_rows,
        "watchlist_competitor_products": watchlist_competitor_rows,
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


def build_product_price_history(
    retailer: str, as_of: date, lookback_days: int = LOOKBACK_DAYS
) -> dict[str, list[dict]]:
    """
    Per-product (by barcode) daily price history for the last
    `lookback_days` published days, so the dashboard can show "how has this
    product's price moved" when someone clicks into it — not just today's
    per-store snapshot.

    Covers the same two row sets already saved in each day's history file —
    P&G's own products and the manager's tracked competitor watchlist
    brands — not the retailer's full catalog, which is what keeps this
    small enough to publish alongside the rest of the dashboard's data.

    Returns: barcode -> list of {date, avg_price, min_price, max_price,
    store_count}, oldest first. A barcode simply has no entry for a day it
    wasn't seen on, rather than a zero/null placeholder — a product on the
    market for only part of the window shouldn't look like it was ever
    priced at zero.
    """
    dates = [d for d in available_dates(retailer) if d <= as_of]
    dates = dates[-lookback_days:]

    series: dict[str, dict[str, list[float]]] = {}

    for d in dates:
        payload = load_day(retailer, d)
        if not payload:
            continue
        rows = list(payload.get("pg_products", [])) + list(
            payload.get("watchlist_competitor_products", [])
        )
        for row in rows:
            barcode = row.get("barcode")
            price = row.get("price")
            if not barcode or price is None:
                continue
            series.setdefault(barcode, {}).setdefault(d.isoformat(), []).append(price)

    result: dict[str, list[dict]] = {}
    for barcode, by_date in series.items():
        points = []
        for iso_date, prices in sorted(by_date.items()):
            points.append(
                {
                    "date": iso_date,
                    "avg_price": round(mean(prices), 2),
                    "min_price": round(min(prices), 2),
                    "max_price": round(max(prices), 2),
                    "store_count": len(prices),
                }
            )
        result[barcode] = points
    return result


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
            info["meta"] = row

        presence_by_date[d] = present_today

    flags = []
    most_recent = dates[-1]
    for key, info in seen.items():
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
                    "last_seen_date": info["last_seen_present"].isoformat(),
                    "first_missing_date": first_missing_date.isoformat() if first_missing_date else None,
                    "days_missing": missing_days,
                    "as_of": most_recent.isoformat(),
                }
            )

    flags.sort(key=lambda f: f["days_missing"], reverse=True)
    return flags