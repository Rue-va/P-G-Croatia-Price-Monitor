#!/usr/bin/env python3
"""
One-off: backfill docs/pricehist/ from the public cijene.dev daily archive
(https://api.cijene.dev/v0/list — the same retailer price lists, collected
daily since 15 May 2025 by the open-source project our crawlers come from;
free to use, provided "as is").

Only P&G products and the tracked competitor products are kept: a product is
included if its barcode is one the dashboard tracks today, or if it is a P&G
brand by the dashboard's own matching rules. Days the dashboard collected
itself are never overwritten.

    python scripts/backfill_price_history.py --from 2025-05-15 --to 2026-10-12
"""
from __future__ import annotations

import argparse
import csv
import gzip
import io
import json
import logging
import sys
import tempfile
import zipfile
from concurrent.futures import ProcessPoolExecutor, as_completed
from datetime import date, timedelta
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from config import DOCS_DIR, RETAILERS  # noqa: E402
from history import (  # noqa: E402
    PRICE_HISTORY_DIR_NAME,
    is_hpc_retail_category,
    match_pg_brand,
    pg_brand_display,
    price_points_from_rows,
    write_price_names,
    write_price_points,
)

ARCHIVE_URL = "https://api.cijene.dev/v0/archive/{day}.zip"
log = logging.getLogger("backfill")


def known_barcodes() -> set[str]:
    """Every barcode the dashboard tracks today, at any retailer."""
    out: set[str] = set()
    for f in (DOCS_DIR / "skus").glob("*.json"):
        out |= {s["ean"] for s in json.loads(f.read_text(encoding="utf-8")) if s.get("ean") and ":" not in s["ean"]}
    for f in (DOCS_DIR / PRICE_HISTORY_DIR_NAME).glob("*/names.json"):
        out |= set(json.loads(f.read_text(encoding="utf-8")))
    return out


def _price(v: str) -> float | None:
    try:
        x = float((v or "").replace(",", "."))
        return x if x > 0 else None
    except ValueError:
        return None


def process_day(day: str, retailers: list[str], known: set[str], out_dir: str) -> tuple[str, dict]:
    """Download one archive day and reduce it to price points per retailer."""
    out_path = Path(out_dir) / f"{day}.json.gz"
    if out_path.exists():
        return day, {"cached": True}
    stats = {}
    with tempfile.TemporaryFile() as tmp:
        with httpx.stream("GET", ARCHIVE_URL.format(day=day), timeout=300, follow_redirects=True) as r:
            if r.status_code == 404:
                return day, {"missing": True}
            r.raise_for_status()
            for chunk in r.iter_bytes(1 << 20):
                tmp.write(chunk)
        tmp.seek(0)
        result = {}
        with zipfile.ZipFile(tmp) as z:
            names_in_zip = set(z.namelist())
            for chain in retailers:
                pfile, cfile = f"{chain}/products.csv", f"{chain}/prices.csv"
                if pfile not in names_in_zip or cfile not in names_in_zip:
                    continue
                products = {}
                with z.open(pfile) as f:
                    for row in csv.DictReader(io.TextIOWrapper(f, encoding="utf-8")):
                        bc = (row.get("barcode") or "").strip()
                        if not bc or ":" in bc:
                            continue
                        name, brand, cat = row.get("name", ""), row.get("brand", ""), row.get("category", "")
                        is_pg = is_hpc_retail_category(cat, name) and match_pg_brand(brand, name)[0]
                        if bc in known or is_pg:
                            products[row["product_id"]] = (bc, name, pg_brand_display(brand, name) if is_pg else brand.strip(), "pg" if is_pg else "c")
                rows = []
                with z.open(cfile) as f:
                    for row in csv.DictReader(io.TextIOWrapper(f, encoding="utf-8")):
                        p = products.get(row["product_id"])
                        if not p:
                            continue
                        price, special = _price(row.get("price")), _price(row.get("special_price"))
                        if price is None and special is None:
                            continue
                        rows.append({
                            "barcode": p[0], "store_id": row["store_id"],
                            "price": price if price is not None else special,
                            "promo_price": special,
                            "regular_price": price if (special is not None and price is not None and price > special + 0.004) else None,
                        })
                points = price_points_from_rows(rows)
                result[chain] = {
                    "points": points,
                    "names": {bc: [n, b, side, None] for bc, n, b, side in products.values() if bc in points},
                }
                stats[chain] = len(points)
    with gzip.open(out_path, "wt", encoding="utf-8") as f:
        json.dump(result, f, separators=(",", ":"))
    return day, stats


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--from", dest="start", default="2025-05-15")
    ap.add_argument("--to", dest="end", default=(date.today() - timedelta(days=1)).isoformat())
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--work-dir", default=str(Path(tempfile.gettempdir()) / "pg_backfill"))
    ap.add_argument("--retailers", default=",".join(RETAILERS))
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")

    retailers = [r.strip() for r in args.retailers.split(",")]
    work = Path(args.work_dir)
    work.mkdir(parents=True, exist_ok=True)
    start, end = date.fromisoformat(args.start), date.fromisoformat(args.end)
    days = [(start + timedelta(days=i)).isoformat() for i in range((end - start).days + 1)]
    known = known_barcodes()
    log.info("Backfilling %d days for %s; %d tracked barcodes", len(days), retailers, len(known))

    # 1) download + reduce, in parallel (each day -> small .json.gz in work dir)
    with ProcessPoolExecutor(max_workers=args.workers) as pool:
        futures = {pool.submit(process_day, d, retailers, known, str(work)): d for d in days}
        for i, fut in enumerate(as_completed(futures), 1):
            d = futures[fut]
            try:
                _, stats = fut.result()
                log.info("[%d/%d] %s %s", i, len(days), d, stats)
            except Exception as e:  # keep going; a re-run picks up missing days
                log.error("[%d/%d] %s failed: %s", i, len(days), d, e)

    # 2) merge month by month into docs/pricehist (never overwriting own days)
    months: dict[str, list[str]] = {}
    for d in days:
        months.setdefault(d[:7], []).append(d)
    for month, mdays in sorted(months.items()):
        per_retailer: dict[str, dict] = {r: {} for r in retailers}
        names: dict[str, dict] = {r: {} for r in retailers}
        for d in mdays:
            path = work / f"{d}.json.gz"
            if not path.exists():
                continue
            with gzip.open(path, "rt", encoding="utf-8") as f:
                data = json.load(f)
            for chain, v in data.items():
                if chain in per_retailer:
                    per_retailer[chain][d] = v["points"]
                    names[chain].update(v["names"])
        for chain in retailers:
            if per_retailer[chain]:
                write_price_points(chain, DOCS_DIR, per_retailer[chain], overwrite=False)
            if names[chain]:
                # don't let archive labels replace the dashboard's own (richer) ones
                existing_path = DOCS_DIR / PRICE_HISTORY_DIR_NAME / chain / "names.json"
                existing = json.loads(existing_path.read_text(encoding="utf-8")) if existing_path.exists() else {}
                write_price_names(chain, DOCS_DIR, {bc: v for bc, v in names[chain].items() if bc not in existing})
        log.info("merged %s", month)


if __name__ == "__main__":
    main()
