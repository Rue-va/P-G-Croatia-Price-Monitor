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
from statistics import mean, median
from typing import Iterable

from config import (
    BRAND_FIELD_CATEGORY_OVERRIDES,
    CATEGORY_BRANDS,
    CATEGORY_COMPARISON_BASIS,
    CATEGORY_DISAMBIGUATION_KEYWORDS,
    HISTORY_DIR,
    LOOKBACK_DAYS,
    MISSING_DAYS_THRESHOLD,
    NON_HPC_RETAIL_CATEGORIES,
    PG_BRAND_DISPLAY,
    PG_BRANDS,
    PRIVATE_LABEL_BRANDS,
    WASH_COUNT_PATTERNS,
)

# Sorted longest-first so "HEAD & SHOULDERS" is tried before any shorter
# brand name that might also appear as a substring of it.
_PG_BRANDS_BY_LENGTH = sorted(PG_BRANDS, key=len, reverse=True)

_word_match_cache: dict[str, re.Pattern] = {}
_wash_count_regexes = [re.compile(p) for p in WASH_COUNT_PATTERNS]


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
    # "&" spacing too: Kaufland writes "Head&Shoulders", the watchlist
    # says "Head & Shoulders" — without this fold the brand silently shows
    # as "not listed" at Kaufland.
    t = (text or "").replace("-", " ")
    t = re.sub(r"\s*&\s*", " & ", t)
    return re.sub(r"\s+", " ", t.strip().upper())


def pg_brand_display(brand: str, product_name: str = "") -> str:
    """
    Canonical P&G brand name for a row already known to be P&G ("ORAL B",
    "Oral-B" and a blank-brand "Oral-B Pro 3" title all -> "Oral-B"), so
    per-brand coverage and promo views don't split one brand three ways.
    """
    norm_brand = _norm(brand)
    key = norm_brand if norm_brand in PG_BRANDS else None
    if key is None:
        norm_product = _norm(product_name)
        key = next((b for b in _PG_BRANDS_BY_LENGTH if _contains_as_word(norm_product, b)), None)
    if key is None:
        return (brand or "").strip()
    return PG_BRAND_DISPLAY.get(key, key.title())


def normalize_unit(unit: str) -> str:
    """
    Fold the retailers' different spellings of the unit-of-measure column
    ("L", "l", "lit", "KOM", "kom.", "KG") onto one short label, so
    per-unit averages are only ever taken over rows with the same unit.
    """
    u = (unit or "").strip().lower().rstrip(".")
    if u in {"l", "lit", "litra", "lt", "ltr"}:
        return "l"
    if u in {"kg", "kilogram"}:
        return "kg"
    if u in {"kom", "komad", "pc", "pcs", "kos", "pak", "par", "kut", "set"}:
        return "kom"
    if u in {"ml", "g", "m"}:
        return u
    return u


_UNIT_TOKENS = {
    "l": ("l", 1.0), "lit": ("l", 1.0), "ml": ("l", 0.001), "cl": ("l", 0.01),
    "kg": ("kg", 1.0), "g": ("kg", 0.001), "gr": ("kg", 0.001),
    "ko": ("kom", 1.0), "kom": ("kom", 1.0), "kos": ("kom", 1.0), "ea": ("kom", 1.0), "pcs": ("kom", 1.0),
}
_QTY_RE = re.compile(r"(\d+(?:[.,]\d+)?)\s*(ml|cl|lit|l|kg|gr|g|kom|kos|ko|ea|pcs)\b\.?", re.I)
_MULTI_RE = re.compile(r"(\d{1,2})\s*[xX×]\s*(\d+(?:[.,]\d+)?)\s*(ml|cl|l|kg|gr|g)\b", re.I)
_COUNT_RE = re.compile(r"(\d{1,4})\s*/\s*1\b|(\d{1,4})\s*(?:kom|kos|tab|kaps|pcs|ks)\b", re.I)


def _parse_size(text: str):
    """(unit, amount) from strings like "450 ml", "2x900ml", "1,35 L", "4.05 l", "30.00 ko", "88/1"."""
    t = text or ""
    m = _MULTI_RE.search(t)
    if m:
        unit, factor = _UNIT_TOKENS[m.group(3).lower()]
        return unit, int(m.group(1)) * float(m.group(2).replace(",", ".")) * factor
    m = _QTY_RE.search(t)
    if m:
        unit, factor = _UNIT_TOKENS[m.group(2).lower()]
        return unit, float(m.group(1).replace(",", ".")) * factor
    m = _COUNT_RE.search(t)
    if m:
        return "kom", float(m.group(1) or m.group(2))
    return None


def infer_unit_price_unit(item) -> str:
    """
    Which unit the retailer's published unit price is per: "l", "kg",
    "kom" (piece), or "pak" when it's simply the pack price repeated.

    The retailers' own "jedinica mjere" column turned out to be the *selling*
    unit (it says "kom"/"ko" on a 900 ml bottle), so it can't be used. The
    pack size is read from the quantity field and the product name, and the
    one that agrees with price ÷ unit_price wins — that cross-check catches
    rows where a field is filled in wrong (e.g. a 900 ml bottle whose
    quantity says "1.00 ko").
    """
    price = float(item.price) if item.price is not None else None
    unit_price = float(item.unit_price) if item.unit_price is not None else None
    candidates = [c for c in (_parse_size(item.quantity), _parse_size(item.product)) if c]
    # Lidl (and Kaufland) give net quantity as a bare number ("0.711"),
    # which is net weight in kg; accept it when price ÷ unit_price agrees.
    bare = re.fullmatch(r"\s*(\d+(?:[.,]\d+)?)\s*", item.quantity or "")
    if bare:
        candidates.append(("kg", float(bare.group(1).replace(",", "."))))
    if price and unit_price:
        implied = price / unit_price
        for unit, amount in candidates:
            if amount > 0 and abs(implied - amount) / amount <= 0.12:
                return unit
        if abs(implied - 1) < 0.01:
            # unit price == pack price: only a true per-unit price if the
            # pack really is 1 l / 1 kg / 1 piece
            if any(abs(a - 1) < 0.01 for _, a in candidates) or not candidates:
                return candidates[0][0] if candidates else "pak"
            return "pak"
    return candidates[0][0] if candidates else ""


def is_hpc_retail_category(category: str) -> bool:
    """False for retailer categories P&G never sells in (food, drinks)."""
    return (category or "").strip().upper() not in NON_HPC_RETAIL_CATEGORIES


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


def extract_wash_count(product_name: str, quantity: str = "") -> int | None:
    """
    Best-effort wash/load count parsed off a laundry or fabric-softener
    product's name (and, as a second try, its quantity field — some
    retailers put "60 PRANJA" there instead of a weight/volume). Returns
    None, never 0, on no match, so callers fall through to the unit-price
    fallback instead of producing a division-by-zero or a nonsense "€/0"
    comparison price.
    """
    haystack = _norm(f"{product_name} {quantity}")
    for regex in _wash_count_regexes:
        m = regex.search(haystack)
        if m:
            count = int(m.group(1))
            if count > 0:
                return count
    return None


def _compute_comparison(category: str, price: float, item) -> tuple[float | None, str, str]:
    """
    Normalize a row's price onto the manager-decided comparison basis for its
    fighting category (config.CATEGORY_COMPARISON_BASIS):
      - "wash" (Laundry, Fabric Enhancers): price per wash, parsed from the
        pack's own stated wash count, since a concentrated formula can use
        far fewer ml/g per wash than a diluted one — €/kg or €/ml alone
        would make a concentrate look overpriced. Falls back to the
        retailer's own published unit price when no wash count can be read
        off the product name.
      - "unit" (everything else): the retailer's own published unit price
        as-is — €/ml, €/kg, €/L or €/piece, whichever applies, exactly as
        required by Croatia's price-transparency rules (NN 75/2025). This is
        what makes "diapers and stuff" come out as €/piece and "liquid
        dishwashers and stuff" come out as €/ml or €/kg, without needing a
        separate pack-count parser for each.

    Returns (comparison_price, comparison_unit, source). `source` records
    how the number was derived ("wash_count", "unit_price", or
    "unit_price_fallback" when a wash count was expected but not found) —
    kept per-row so the aggregate can report how much of a category's
    comparison rests on a real parsed wash count vs. a fallback, rather than
    presenting both as equally solid.
    """
    basis = CATEGORY_COMPARISON_BASIS.get(category, "unit")
    unit = infer_unit_price_unit(item)
    unit_price = float(item.unit_price) if item.unit_price is not None else None

    if basis == "wash":
        wash_count = extract_wash_count(item.product, item.quantity)
        if wash_count:
            return round(price / wash_count, 4), "pranje", "wash_count"
        if unit == "pak":
            return None, unit, "unit_price_fallback"
        if unit == "kom":
            # pods / tablets: one piece is one wash, so €/piece is €/wash
            return (round(unit_price, 4) if unit_price is not None else None), "pranje", "unit_price_per_piece"
        return (round(unit_price, 4) if unit_price is not None else None), unit, "unit_price_fallback"

    if unit == "pak":
        # pack price, not a per-unit price — not comparable across pack sizes
        return None, unit, "unit_price"
    return (round(unit_price, 4) if unit_price is not None else None), unit, "unit_price"


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

            # Manager-curated brand watchlist (config.CATEGORY_BRANDS): match
            # on the brand field first, then fall back to spotting the brand
            # name inside the product title, same tolerant approach as
            # match_pg_brand below. row_fighting_category is stashed onto the
            # P&G row itself (below) so the dashboard can filter the SKU list
            # by the same "fighting category" taxonomy as the competitor
            # tracker, not just by the retailer's own raw category string.
            row_fighting_category = None
            # A promotion ("poseban oblik prodaje") is published as a
            # separate, lower special price next to the regular one. Only
            # kept when it's genuinely below the regular price, since some
            # files repeat the regular price in that column.
            # Konzum leaves the regular price blank on promo rows (the crawler
            # then copies the promo price into `price`), so any filled-in
            # special price counts; regular_price is only known when the
            # file also gives a higher regular price.
            special = float(item.special_price) if item.special_price is not None else None
            promo_price = special if (special is not None and special > 0) else None
            regular_price = price if (promo_price is not None and price is not None and price > promo_price + 0.004) else None
            comparison_price = None
            comparison_unit = ""
            comparison_source = ""
            hpc_row = is_hpc_retail_category(category)
            if price is not None and hpc_row:
                # Some retailers' own brand field already spells out which
                # product line a row is (see BRAND_FIELD_CATEGORY_OVERRIDES
                # in config.py) — that's a more reliable signal than
                # guessing from the product name, so it's checked first and
                # skips the keyword disambiguation below entirely.
                watch_brand_key = None
                target_cat = None

                override = BRAND_FIELD_CATEGORY_OVERRIDES.get(norm_brand)
                if override:
                    override_brand_display, override_cat = override
                    override_norm_brand = _norm(override_brand_display)
                    if override_norm_brand in watch_prices.get(override_cat, {}):
                        watch_brand_key, target_cat = override_norm_brand, override_cat
                else:
                    matched_watch_brand = norm_brand if norm_brand in watch_brand_to_categories else None
                    if matched_watch_brand is None and norm_product:
                        for wb in watch_brand_by_length:
                            if _contains_as_word(norm_product, wb):
                                matched_watch_brand = wb
                                break
                    if matched_watch_brand:
                        cats_for_brand = watch_brand_to_categories[matched_watch_brand]
                        brand_rules = CATEGORY_DISAMBIGUATION_KEYWORDS.get(matched_watch_brand, {})
                        if len(cats_for_brand) == 1 and cats_for_brand[0] not in brand_rules:
                            cat_candidate = cats_for_brand[0]
                        else:
                            cat_candidate = None
                            for cat in cats_for_brand:
                                rule = brand_rules.get(cat, [])
                                if _keyword_rule_matches(norm_product, rule):
                                    cat_candidate = cat
                                    break
                        if cat_candidate:
                            watch_brand_key, target_cat = matched_watch_brand, cat_candidate

                if watch_brand_key and target_cat:
                    # Normalize this row onto its category's comparison basis
                    # (€/wash, €/ml, €/kg, €/piece — see _compute_comparison)
                    # before stashing it, rather than the raw shelf price, so
                    # category_brand_watchlist below can report a like-for-like
                    # average instead of one that rewards whoever sells the
                    # smaller pack.
                    comparison_price, comparison_unit, comparison_source = _compute_comparison(
                        target_cat, price, item
                    )
                    watch_prices[target_cat][watch_brand_key].append(
                        {
                            "price": price,
                            "promo_price": promo_price,
                            "comparison_price": comparison_price,
                            "comparison_unit": comparison_unit,
                            "comparison_source": comparison_source,
                            "store_id": store.store_id,
                            "barcode": item.barcode,
                        }
                    )
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

            matched, via = match_pg_brand(item.brand, item.product) if hpc_row else (False, "")
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
                        "pg_brand": pg_brand_display(item.brand, item.product),
                        "fighting_category": row_fighting_category,
                        "price": price,
                        "promo_price": promo_price,
                        "regular_price": regular_price,
                        "quantity": item.quantity,
                        "unit": infer_unit_price_unit(item),
                        "unit_price": float(item.unit_price) if item.unit_price is not None else None,
                        "comparison_price": comparison_price,
                        "comparison_unit": comparison_unit,
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
                if norm_brand:
                    # Track named competitor brands per category (e.g.
                    # Persil within Ariel's laundry category), scoped to
                    # categories P&G actually competes in — unlike the
                    # catalog-wide brand diagnostics below, this doesn't get
                    # crowded out by unrelated grocery brands.
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
                            "promo_price": promo_price,
                            "regular_price": regular_price,
                            "quantity": item.quantity,
                            "unit": infer_unit_price_unit(item),
                            "unit_price": float(item.unit_price) if item.unit_price is not None else None,
                            "comparison_price": comparison_price,
                            "comparison_unit": comparison_unit,
                            "barcode": item.barcode,
                        }
                    )

    # collapse raw price lists into summary numbers before saving
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

    # Collapse the manager-curated brand watchlist into a display-ready
    # structure: every configured brand appears for every configured
    # category, even at count=0, so a brand that genuinely isn't sold today
    # is visibly "not found" rather than silently absent from the page.
    category_brand_watchlist: dict[str, dict] = {}
    for cat, brands in watch_prices.items():
        basis = CATEGORY_COMPARISON_BASIS.get(cat, "unit")
        pg_brands, competitor_brands_out = [], []
        for norm, entries in brands.items():
            display = watch_brand_display[norm]
            prices = [e["price"] for e in entries]
            comparison_prices = [e["comparison_price"] for e in entries if e["comparison_price"] is not None]
            # Entries normally share one unit per brand/category (e.g. every
            # Pampers diaper row is €/piece); Counter just guards against a
            # stray retailer row using a different unit than the rest.
            unit_counts = Counter(e["comparison_unit"] for e in entries if e["comparison_unit"])
            comparison_unit = unit_counts.most_common(1)[0][0] if unit_counts else ""
            # Only meaningful for "wash"-basis categories: what share of this
            # brand's rows today got a real parsed wash count vs. fell back
            # to the retailer's own unit price — lets Rue tell a solid €/wash
            # number from a mostly-fallback one at a glance.
            wash_coverage = None
            if basis == "wash" and entries:
                wash_based = sum(1 for e in entries if e["comparison_source"] == "wash_count")
                wash_coverage = round(100 * wash_based / len(entries), 1)
            # Per-unit averages: a category can mix units (toothbrushes are
            # €/piece, toothpaste €/l), and averaging €/piece with €/l is
            # meaningless — the dashboard compares brands within one unit.
            by_unit: dict[str, list[float]] = {}
            for e in entries:
                if e["comparison_price"] is not None and e["comparison_unit"]:
                    by_unit.setdefault(e["comparison_unit"], []).append(e["comparison_price"])
            promo_rows = sum(1 for e in entries if e.get("promo_price") is not None)
            entry = {
                "brand": display,
                "count": len(prices),
                "avg_price": round(mean(prices), 2) if prices else None,
                "comparison_avg_price": round(mean(comparison_prices), 4) if comparison_prices else None,
                "comparison_unit": comparison_unit,
                "comparison_basis": basis,
                "wash_count_coverage_pct": wash_coverage,
                "by_unit": {
                    u: {"avg": round(mean(v), 4), "median": round(median(v), 4), "count": len(v)}
                    for u, v in by_unit.items()
                },
                "store_count": len({e["store_id"] for e in entries}),
                "sku_count": len({e["barcode"] for e in entries if e.get("barcode")}),
                "promo_rows": promo_rows,
                "promo_pct": round(100 * promo_rows / len(entries), 1) if entries else 0,
            }
            (pg_brands if is_pg(display) else competitor_brands_out).append(entry)
        category_brand_watchlist[cat] = {
            "pg_brands": pg_brands,
            "competitor_brands": competitor_brands_out,
            "comparison_basis": basis,
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

    # barcode -> date (iso string) -> list of prices seen that day (across stores)
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

    # product_key -> {"first_seen": date, "last_seen": date, "meta": {...}}
    seen: dict[tuple, dict] = {}
    presence_by_date: dict[date, set] = {}
    # Which stores published a price list at all on each day. Retailers
    # don't always publish every store every day (Konzum's file count has
    # swung between ~70 and ~107 stores day to day); a store with no file
    # that day says nothing about its shelves, so that day is skipped
    # rather than counted as "missing" for every product it carries.
    stores_by_date: dict[date, set] = {}

    for d in dates:
        payload = load_day(retailer, d)
        if not payload:
            continue
        present_today = set()
        stores_today = set()
        for row in payload["pg_products"] + payload.get("watchlist_competitor_products", []):
            stores_today.add(row["store_id"])
        stores_by_date[d] = stores_today
        for row in payload["pg_products"]:
            # Re-apply today's matching rules to older snapshots: rows that
            # were wrongly counted as P&G back then (squid "LIGNJA JAR",
            # wine "VINO VENUS", caviar …) would otherwise all show up as
            # "missing" the day the matching was fixed.
            if not is_hpc_retail_category(row.get("category", "")) or not match_pg_brand(row.get("brand", ""), row.get("product", ""))[0]:
                continue
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
            if key[0] not in stores_by_date.get(d, set()):
                continue
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

def _row_brand(row: dict) -> str:
    return row.get("pg_brand") or (row.get("brand") or "").strip()


def build_insights(retailer: str, as_of: date, payload: dict, flags: list[dict]) -> dict:
    """
    Small, dashboard-ready summaries computed once per run, so the page can
    show them without downloading the multi-megabyte per-store product
    files:
      - price_moves: SKUs (P&G + tracked competitors) whose regular price
        changed since the previous published day, compared store-by-store
        (same store, same barcode) so a store that simply didn't publish
        one day can't masquerade as a price change.
      - promos: SKUs on a published promotion today, with depth and reach.
      - pg_brand_coverage: for each P&G brand, how many of the retailer's
        stores list it and how many SKUs.
      - flags_by_store: the missing-item flags rolled up per store, which
        reads far better than thousands of individual rows.
    """
    rows_today = [dict(r, side="pg") for r in payload.get("pg_products", [])] + [
        dict(r, side="competitor") for r in payload.get("watchlist_competitor_products", [])
    ]

    # --- price moves vs. previous published day -------------------------
    prev_dates = [d for d in available_dates(retailer) if d < as_of]
    price_moves: list[dict] = []
    implausible_moves = 0
    prev_date_iso = None
    if prev_dates:
        prev = load_day(retailer, prev_dates[-1]) or {}
        prev_date_iso = prev_dates[-1].isoformat()
        prev_price: dict[tuple, float] = {}
        for r in prev.get("pg_products", []) + prev.get("watchlist_competitor_products", []):
            if r.get("barcode") and r.get("price") is not None and r.get("promo_price") is None:
                prev_price[(r["store_id"], r["barcode"])] = r["price"]

        by_barcode: dict[str, dict] = {}
        for r in rows_today:
            # promo rows are covered by the promo list; comparing them here
            # would report every promo start/end as a "price change"
            if not r.get("barcode") or r.get("price") is None or r.get("promo_price") is not None:
                continue
            old = prev_price.get((r["store_id"], r["barcode"]))
            if old is None or old <= 0:
                continue
            g = by_barcode.setdefault(r["barcode"], {"row": r, "pairs": []})
            g["pairs"].append((old, r["price"]))

        for barcode, g in by_barcode.items():
            # A real regular-price change is a few percent, rarely more than
            # ~50%. A 5x-10x jump (e.g. €0.54 -> €4.99) is a retailer file
            # glitch or a placeholder price, so it's counted but not listed.
            changed = [(o, n) for o, n in g["pairs"] if abs(n - o) >= 0.01]
            if changed and max(abs(n - o) / o for o, n in changed) >= 0.6:
                implausible_moves += 1
                continue
            if not changed:
                continue
            r = g["row"]
            olds = [o for o, _ in changed]
            news = [n for _, n in changed]
            old_med, new_med = median(olds), median(news)
            price_moves.append({
                "barcode": barcode,
                "product": r["product"],
                "brand": _row_brand(r),
                "side": r["side"],
                "fighting_category": r.get("fighting_category"),
                "prev_price": round(old_med, 2),
                "price": round(new_med, 2),
                "pct": round(100 * (new_med - old_med) / old_med, 1),
                "stores_changed": len(changed),
                "stores_compared": len(g["pairs"]),
            })
        price_moves.sort(key=lambda m: (m["stores_changed"] * abs(m["pct"])), reverse=True)

    # --- promotions today -----------------------------------------------
    # Konzum and Kaufland leave the regular price blank on promo rows, so the
    # regular price is estimated as the same SKU's median non-promo price in
    # this retailer's other stores today.
    non_promo: dict[str, list[float]] = {}
    for r in rows_today:
        if r.get("barcode") and r.get("promo_price") is None and r.get("price") is not None:
            non_promo.setdefault(r["barcode"], []).append(r["price"])
    promo_groups: dict[str, dict] = {}
    for r in rows_today:
        if r.get("promo_price") is None or not r.get("barcode"):
            continue
        g = promo_groups.setdefault(r["barcode"], {"row": r, "regular": [], "promo": [], "stores": set()})
        if r.get("regular_price") is not None:
            g["regular"].append(r["regular_price"])
        g["promo"].append(r["promo_price"])
        g["stores"].add(r["store_id"])
    promos = []
    for barcode, g in promo_groups.items():
        r = g["row"]
        pro = median(g["promo"])
        reg_source = "published"
        if g["regular"]:
            reg = median(g["regular"])
        elif non_promo.get(barcode):
            reg, reg_source = median(non_promo[barcode]), "other_stores"
        else:
            reg, reg_source = None, None
        if reg is not None and reg <= pro + 0.004:
            reg, reg_source = None, None
        promos.append({
            "barcode": barcode,
            "product": r["product"],
            "brand": _row_brand(r),
            "side": r["side"],
            "fighting_category": r.get("fighting_category"),
            "regular_price": round(reg, 2) if reg is not None else None,
            "regular_price_source": reg_source,
            "promo_price": round(pro, 2),
            "depth_pct": round(100 * (reg - pro) / reg, 1) if reg else None,
            "store_count": len(g["stores"]),
        })
    promos.sort(key=lambda p: (p["side"] != "pg", -p["store_count"], -(p["depth_pct"] or 0)))

    # --- P&G brand coverage ----------------------------------------------
    total_stores = payload.get("stores_count") or 0
    cov: dict[str, dict] = {}
    for r in payload.get("pg_products", []):
        b = _row_brand(r)
        c = cov.setdefault(b, {"stores": set(), "skus": set(), "rows": 0, "promo_rows": 0})
        c["stores"].add(r["store_id"])
        if r.get("barcode"):
            c["skus"].add(r["barcode"])
        c["rows"] += 1
        if r.get("promo_price") is not None:
            c["promo_rows"] += 1
    pg_brand_coverage = sorted(
        (
            {
                "brand": b,
                "stores": len(c["stores"]),
                "total_stores": total_stores,
                "store_pct": round(100 * len(c["stores"]) / total_stores, 1) if total_stores else None,
                "skus": len(c["skus"]),
                "avg_skus_per_store": round(c["rows"] / len(c["stores"]), 1) if c["stores"] else 0,
                "promo_pct": round(100 * c["promo_rows"] / c["rows"], 1) if c["rows"] else 0,
            }
            for b, c in cov.items()
        ),
        key=lambda x: -x["skus"],
    )

    # --- flags rolled up per store --------------------------------------
    by_store: dict[str, dict] = {}
    for f in flags:
        s = by_store.setdefault(f["store_id"], {
            "store_id": f["store_id"], "store_name": f["store_name"], "city": f.get("city", ""),
            "items": 0, "max_days": 0, "brands": Counter(),
        })
        s["items"] += 1
        s["max_days"] = max(s["max_days"], f["days_missing"])
        s["brands"][f["brand"]] += 1
    flags_by_store = sorted(
        (
            {**{k: v for k, v in s.items() if k != "brands"},
             "top_brands": [b for b, _ in s["brands"].most_common(3)]}
            for s in by_store.values()
        ),
        key=lambda s: -s["items"],
    )

    # --- flags rolled up per SKU: "which products are missing where" ------
    by_sku: dict[str, dict] = {}
    for f in flags:
        key = f.get("product_id") or f["product"]
        s_ = by_sku.setdefault(key, {
            "product": f["product"], "brand": f["brand"], "stores": 0,
            "max_days": 0, "last_seen_price": f.get("last_seen_price"), "store_names": [],
        })
        s_["stores"] += 1
        s_["max_days"] = max(s_["max_days"], f["days_missing"])
        if len(s_["store_names"]) < 8:
            s_["store_names"].append(f"{f['store_name']}{' (' + f['city'] + ')' if f.get('city') else ''}")
    flags_by_sku = sorted(by_sku.values(), key=lambda x: -x["stores"])

    return {
        "compared_with_date": prev_date_iso,
        "flagged_skus_total": len(flags_by_sku),
        "flags_by_sku": flags_by_sku[:150],
        "price_moves_total": len(price_moves),
        "price_moves_excluded_implausible": implausible_moves,
        "price_moves": price_moves[:80],
        "promos_total": len(promos),
        "promos_pg_total": sum(1 for p in promos if p["side"] == "pg"),
        "promos": promos[:300],
        "pg_brand_coverage": pg_brand_coverage,
        "flags_by_store": flags_by_store[:100],
    }


def build_sku_table(payload: dict) -> list[dict]:
    """
    One row per SKU (barcode) at this retailer — P&G's whole range plus the
    tracked competitor brands — with its median shelf price, median
    like-for-like unit price and how many stores list it / run it on promo.

    This is what lets the dashboard show *actual products* (every SKU as a
    dot on a price scale, the same EAN compared across retailers) instead
    of one blended category average that a single odd pack size can skew.
    """
    groups: dict[str, dict] = {}
    rows = [dict(r, side="pg") for r in payload.get("pg_products", [])] + [
        dict(r, side="competitor") for r in payload.get("watchlist_competitor_products", [])
    ]
    for r in rows:
        key = r.get("barcode") or f"{r['side']}:{r['product']}"
        g = groups.setdefault(key, {"r": r, "shelf": [], "unit": [], "units": Counter(),
                                    "promo": [], "stores": set(), "promo_stores": set()})
        if r.get("price") is not None:
            # shelf = regular price where known, so a promo doesn't make a
            # SKU look structurally cheap; promos are reported separately
            g["shelf"].append(r.get("regular_price") or r["price"])
        if r.get("comparison_price") is not None and r.get("comparison_unit"):
            g["unit"].append(r["comparison_price"])
            g["units"][r["comparison_unit"]] += 1
        g["stores"].add(r["store_id"])
        if r.get("promo_price") is not None:
            g["promo_stores"].add(r["store_id"])
            g["promo"].append(r["promo_price"])
    out = []
    for key, g in groups.items():
        r = g["r"]
        unit = g["units"].most_common(1)[0][0] if g["units"] else ""
        out.append({
            "ean": r.get("barcode") or "",
            "product": r["product"],
            "brand": r.get("pg_brand") or (r.get("brand") or "").strip(),
            "side": r["side"],
            "cat": r.get("fighting_category"),
            "shelf": round(median(g["shelf"]), 2) if g["shelf"] else None,
            "unit_price": round(median(g["unit"]), 4) if g["unit"] else None,
            "unit": unit,
            "promo": round(median(g["promo"]), 2) if g["promo"] else None,
            "stores": len(g["stores"]),
            "promo_stores": len(g["promo_stores"]),
        })
    return out
