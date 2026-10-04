#!/usr/bin/env python3
"""
Daily job: crawl all configured retailers, save today's history snapshot,
recompute missing-item flags, and write docs/data.json for the dashboard.

Run manually:
    python scrape_and_export.py [--date YYYY-MM-DD]

In production this is run once a day by .github/workflows/daily.yml.
Each retailer is wrapped in its own try/except so one retailer breaking
(a site redesign, a network hiccup) doesn't take down the whole run — it's
logged and the dashboard just shows stale data for that retailer with a
clear "last updated" timestamp, rather than the whole page going blank.
"""
from __future__ import annotations

import argparse
import importlib
import json
import logging
import sys
import traceback
from datetime import date, datetime, timezone

from config import DASHBOARD_DATA_FILE, DOCS_DIR, MISSING_DAYS_THRESHOLD, RETAILERS
from history import (
    build_day_payload,
    build_insights,
    build_sku_table,
    build_product_price_history,
    compute_missing_flags,
    save_day,
    load_day,
    available_dates,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
log = logging.getLogger("scrape_and_export")


def get_crawler(retailer: str):
    module = importlib.import_module(f"crawler_vendor.{retailer}")
    class_name = f"{retailer.capitalize()}Crawler"
    return getattr(module, class_name)()


def write_sku_table(retailer: str, payload: dict) -> None:
    skus_path = DOCS_DIR / "skus"
    skus_path.mkdir(parents=True, exist_ok=True)
    (skus_path / f"{retailer}.json").write_text(
        json.dumps(build_sku_table(payload), ensure_ascii=False, separators=(",", ":")), encoding="utf-8"
    )


def run_retailer(retailer: str, target_date: date, from_history: bool = False) -> dict:
    """
    Returns a dashboard-ready summary dict for this retailer, and saves the
    raw day payload + flags to disk as a side effect. Never raises: on
    failure, returns a status="error" summary so the rest of the run and
    the dashboard's other retailers are unaffected.
    """
    try:
        log.info("Starting crawl: %s", retailer)
        if from_history:
            payload = load_day(retailer, target_date)
            if not payload or not payload.get("stores_count"):
                raise RuntimeError(f"no usable saved snapshot for {target_date}")
        else:
            crawler = get_crawler(retailer)
            stores = crawler.get_all_products(target_date)
            payload = build_day_payload(retailer, target_date, stores)
            # A retailer that hasn't published yet (or a broken crawl) can
            # come back with zero stores. Saving that as a real day made
            # the whole retailer look empty on the dashboard (Kaufland,
            # 4 Oct) — treat it as a failed crawl and fall back instead.
            prev_dates = [d for d in available_dates(retailer) if d < target_date]
            prev = load_day(retailer, prev_dates[-1]) if prev_dates else None
            prev_stores = (prev or {}).get("stores_count") or 0
            if payload["stores_count"] == 0 or (prev_stores and payload["stores_count"] < 0.25 * prev_stores):
                raise RuntimeError(
                    f"only {payload['stores_count']} stores published (previous day: {prev_stores}) "
                    "— treating as not yet published"
                )
            save_day(retailer, target_date, payload)
        log.info(
            "%s: %d stores, %d total products, %d P&G rows",
            retailer, payload["stores_count"], payload["total_products_all_stores"],
            payload["pg_product_rows"],
        )

        flags = compute_missing_flags(retailer, target_date, threshold=MISSING_DAYS_THRESHOLD)
        flags_path = DOCS_DIR / "flags"
        flags_path.mkdir(parents=True, exist_ok=True)
        (flags_path / f"{retailer}.json").write_text(
            json.dumps(flags, ensure_ascii=False, indent=1), encoding="utf-8"
        )

        # Full P&G product list for the "Products tracked" tab — kept as its
        # own file (not inlined in data.json) so the main dashboard payload
        # stays small even when a retailer has tens of thousands of P&G rows.
        products_path = DOCS_DIR / "products"
        products_path.mkdir(parents=True, exist_ok=True)
        (products_path / f"{retailer}.json").write_text(
            json.dumps(payload["pg_products"], ensure_ascii=False, indent=1), encoding="utf-8"
        )

        # Same idea, but for the (much smaller) set of watchlisted competitor
        # brands — this is what lets the dashboard drill into store-level
        # detail when someone clicks a competitor brand chip in the
        # Competitor Tracker, not just P&G's own brands.
        (products_path / f"{retailer}_watchlist_competitors.json").write_text(
            json.dumps(payload.get("watchlist_competitor_products", []), ensure_ascii=False, indent=1),
            encoding="utf-8",
        )

        # Per-product (by barcode) price-over-time series, covering P&G's
        # own products and the tracked competitor watchlist — powers the
        # price history chart shown when someone clicks into an individual
        # product, rather than only ever showing today's snapshot.
        history_path = DOCS_DIR / "history"
        history_path.mkdir(parents=True, exist_ok=True)
        price_history = build_product_price_history(retailer, target_date)
        write_sku_table(retailer, payload)
        (history_path / f"{retailer}.json").write_text(
            json.dumps(price_history, ensure_ascii=False, indent=1), encoding="utf-8"
        )

        return {
            "retailer": retailer,
            "status": "ok",
            "last_updated": target_date.isoformat(),
            "stores_count": payload["stores_count"],
            "total_catalog_size": payload["total_products_all_stores"],
            "pg_products_tracked": payload["pg_product_rows"],
            "category_stats": payload["category_stats"],
            "category_brand_watchlist": payload.get("category_brand_watchlist", {}),
            "diagnostics": payload["diagnostics"],
            "flagged_count": len(flags),
            "flagged_items": flags[:200],  # cap so data.json stays light
            "history_days_available": len(available_dates(retailer)),
            "insights": build_insights(retailer, target_date, payload, flags),
        }
    except Exception as exc:
        log.error("Retailer %s failed: %s", retailer, exc, exc_info=True)
        # Fall back to the most recent successful snapshot we have, if any,
        # so the dashboard degrades gracefully instead of losing a retailer.
        for d in reversed(available_dates(retailer)):
            prev = load_day(retailer, d)
            if prev and prev.get("stores_count"):
                write_sku_table(retailer, prev)
                flags = compute_missing_flags(retailer, d, threshold=MISSING_DAYS_THRESHOLD)
                return {
                    "retailer": retailer,
                    "status": "stale",
                    "error": str(exc),
                    "last_updated": d.isoformat(),
                    "stores_count": prev["stores_count"],
                    "total_catalog_size": prev["total_products_all_stores"],
                    "pg_products_tracked": prev["pg_product_rows"],
                    "category_stats": prev["category_stats"],
                    "category_brand_watchlist": prev.get("category_brand_watchlist", {}),
                    "diagnostics": prev.get("diagnostics", {}),
                    "flagged_count": len(flags),
                    "flagged_items": flags[:200],
                    "history_days_available": len(available_dates(retailer)),
                    "insights": build_insights(retailer, d, prev, flags),
                }
        return {
            "retailer": retailer,
            "status": "error",
            "error": str(exc),
            "last_updated": None,
        }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--date", type=str, default=None, help="YYYY-MM-DD, default today")
    parser.add_argument("--retailers", type=str, default=None, help="Comma-separated subset")
    parser.add_argument("--from-history", action="store_true",
                        help="Rebuild dashboard files from the saved snapshot instead of crawling")
    args = parser.parse_args()

    target_date = date.fromisoformat(args.date) if args.date else date.today()
    retailers = args.retailers.split(",") if args.retailers else RETAILERS

    results = []
    for retailer in retailers:
        results.append(run_retailer(retailer.strip(), target_date, from_history=args.from_history))

    dashboard = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "as_of_date": target_date.isoformat(),
        "missing_days_threshold": MISSING_DAYS_THRESHOLD,
        "retailers": results,
    }

    DOCS_DIR.mkdir(parents=True, exist_ok=True)
    DASHBOARD_DATA_FILE.write_text(
        json.dumps(dashboard, ensure_ascii=False, indent=1), encoding="utf-8"
    )
    log.info("Wrote dashboard data to %s", DASHBOARD_DATA_FILE)

    n_ok = sum(1 for r in results if r["status"] == "ok")
    n_stale = sum(1 for r in results if r["status"] == "stale")
    n_err = sum(1 for r in results if r["status"] == "error")
    log.info("Done: %d ok, %d stale (using last good data), %d hard errors", n_ok, n_stale, n_err)

    # Non-zero exit only if EVERY retailer failed outright, so a partial
    # failure still lets the workflow commit the partial update.
    if n_err == len(results):
        sys.exit(1)


if __name__ == "__main__":
    main()
