#!/usr/bin/env python3
"""
Builds docs/PG_Price_Report.xlsx from the same data the dashboard already
produced (docs/data.json, docs/products/<retailer>.json,
docs/flags/<retailer>.json). Run this AFTER scrape_and_export.py, in the
same daily job, so both outputs always describe the same day.

Publishing the workbook into docs/ means GitHub Pages serves it directly —
one link on the dashboard gives the manager a plain .xlsx download, with no
separate process or server needed for it.

Sheet layout:
  - "Summary": one row per retailer — catalog size, P&G count, stores,
    flagged count — plus a bar chart comparing retailers at a glance.
  - "<Retailer> Categories": P&G vs. competitor average price per category,
    plus a bar chart — the Excel equivalent of the dashboard's price chart.
  - "<Retailer> Competitor Tracker": the manager-curated brand watchlist
    (config.CATEGORY_BRANDS) — every tracked P&G/competitor brand per
    fighting category, one row each, even at 0 rows seen today.
  - "<Retailer> P&G assortment": every P&G product tracked, one row per
    store (this is the same data as the dashboard's "Products tracked"
    table, just delivered as a spreadsheet instead).
  - "<Retailer> Flagged": items missing 3+ consecutive published days.

Kept intentionally light: P&G-only rows (typically hundreds to a few
thousand per retailer, not the retailer's full catalog), plain openpyxl
Tables rather than per-cell styling loops, so this runs in seconds even
across four retailers — the earlier version of this project hit a real
95-second/25k-row slowdown from per-cell styling at full-catalog scale;
this design avoids that by construction.
"""
from __future__ import annotations

import json
from pathlib import Path

from openpyxl import Workbook
from openpyxl.chart import BarChart, Reference
from openpyxl.styles import Font, PatternFill
from openpyxl.utils import get_column_letter
from openpyxl.worksheet.table import Table, TableStyleInfo

from config import DOCS_DIR

HEADER_FILL = PatternFill(start_color="004B93", end_color="004B93", fill_type="solid")
HEADER_FONT = Font(color="FFFFFF", bold=True)
WARN_FILL = PatternFill(start_color="FFF4E5", end_color="FFF4E5", fill_type="solid")

REPORT_PATH = DOCS_DIR / "PG_Price_Report.xlsx"


def _safe_sheet_name(name: str) -> str:
    # Excel sheet names: max 31 chars, no []:*?/\\
    for ch in "[]:*?/\\":
        name = name.replace(ch, "")
    return name[:31]


def _write_table(ws, headers: list[str], rows: list[list], table_name: str):
    ws.append(headers)
    for cell in ws[1]:
        cell.fill = HEADER_FILL
        cell.font = HEADER_FONT
    for row in rows:
        ws.append(row)

    n_rows = len(rows) + 1
    n_cols = len(headers)
    if n_rows > 1:
        last_col = get_column_letter(n_cols)
        table_ref = f"A1:{last_col}{n_rows}"
        table = Table(displayName=table_name, ref=table_ref)
        table.tableStyleInfo = TableStyleInfo(
            name="TableStyleMedium2", showRowStripes=True
        )
        ws.add_table(table)

    for col_idx, header in enumerate(headers, start=1):
        width = max(12, min(45, len(header) + 4))
        ws.column_dimensions[get_column_letter(col_idx)].width = width


def build_report():
    data_path = DOCS_DIR / "data.json"
    if not data_path.exists():
        raise SystemExit("docs/data.json not found — run scrape_and_export.py first")
    dashboard = json.loads(data_path.read_text(encoding="utf-8"))

    wb = Workbook()
    summary_ws = wb.active
    summary_ws.title = "Summary"

    summary_headers = [
        "Retailer", "Status", "Last Updated", "Total Catalog Size",
        "P&G Products Tracked", "Stores Covered", "Flagged (missing 3+ days)",
    ]
    summary_rows = []

    for r in dashboard.get("retailers", []):
        retailer = r["retailer"]
        status = r.get("status", "error")
        summary_rows.append([
            retailer.title(),
            status,
            r.get("last_updated") or "—",
            r.get("total_catalog_size", 0),
            r.get("pg_products_tracked", 0),
            r.get("stores_count", 0),
            r.get("flagged_count", 0),
        ])

        if status == "error":
            continue  # nothing to build a products/flagged sheet from

        # --- Categories sheet (P&G vs. competitor pricing) + chart ---
        category_stats = r.get("category_stats") or {}
        if category_stats:
            cat_ws = wb.create_sheet(_safe_sheet_name(f"{retailer.title()} Categories"))
            cat_rows = []
            for cat, cs in sorted(category_stats.items()):
                top_brands = cs.get("top_competitor_brands") or []
                brands_text = "; ".join(
                    f"{b['brand']} (€{b['avg_price']:.2f}, {b['count']}x)" for b in top_brands
                )
                cat_rows.append([
                    cat,
                    cs.get("count", 0),
                    cs.get("pg_count", 0),
                    cs.get("pg_avg_price"),
                    cs.get("competitor_avg_price"),
                    cs.get("competitor_count", 0),
                    brands_text,
                ])
            _write_table(
                cat_ws,
                ["Category", "All-Store Item Count", "P&G Item Count",
                 "P&G Avg Price (EUR)", "Competitor Avg Price excl. P&G (EUR)",
                 "Competitor Item Count", "Named Competitor Brands (avg price, rows seen)"],
                cat_rows,
                table_name=_safe_sheet_name(f"{retailer}_categories").replace(" ", "_"),
            )
            # Only categories where P&G actually has a price make a meaningful
            # bar in a P&G-vs-competitor comparison.
            n_cat = len(cat_rows)
            if n_cat:
                chart = BarChart()
                chart.type = "col"
                chart.grouping = "clustered"
                chart.title = f"{retailer.title()}: P&G vs. competitor avg price by category"
                chart.y_axis.title = "Avg price (EUR)"
                chart.x_axis.title = "Category"
                data_ref = Reference(cat_ws, min_col=4, max_col=5, min_row=1, max_row=n_cat + 1)
                cats_ref = Reference(cat_ws, min_col=1, min_row=2, max_row=n_cat + 1)
                chart.add_data(data_ref, titles_from_data=True)
                chart.set_categories(cats_ref)
                chart.width, chart.height = 24, 11
                cat_ws.add_chart(chart, f"A{n_cat + 4}")

        # --- Competitor Tracker sheet: the manager-curated brand watchlist,
        # one row per tracked brand so it's filterable/sortable, with every
        # configured brand present even at 0 rows/no price (so "not sold
        # today" is visible instead of just missing from the sheet). ---
        watchlist = r.get("category_brand_watchlist") or {}
        if watchlist:
            wl_ws = wb.create_sheet(_safe_sheet_name(f"{retailer.title()} Competitor Tracker"))
            wl_rows = []
            for cat, groups in watchlist.items():
                for b in groups.get("pg_brands", []):
                    wl_rows.append([cat, "P&G", b["brand"], b["count"], b["avg_price"]])
                for b in groups.get("competitor_brands", []):
                    wl_rows.append([cat, "Competitor", b["brand"], b["count"], b["avg_price"]])
            _write_table(
                wl_ws,
                ["Category", "Side", "Brand", "Rows Seen Today", "Avg Price (EUR)"],
                wl_rows,
                table_name=_safe_sheet_name(f"{retailer}_watchlist").replace(" ", "_"),
            )
            # Flag brands with zero rows today — either genuinely not sold,
            # or a brand-name spelling mismatch worth double-checking.
            for row_idx, row in enumerate(wl_rows, start=2):
                if row[3] == 0:
                    for col_idx in range(1, 6):
                        wl_ws.cell(row=row_idx, column=col_idx).fill = WARN_FILL

        # --- P&G assortment sheet ---
        products_path = DOCS_DIR / "products" / f"{retailer}.json"
        if products_path.exists():
            products = json.loads(products_path.read_text(encoding="utf-8"))
            ws = wb.create_sheet(_safe_sheet_name(f"{retailer.title()} P&G"))
            _write_table(
                ws,
                ["Product", "Brand", "Category", "EAN", "Store", "Address", "City", "Price (EUR)"],
                [
                    [p.get("product"), p.get("brand"), p.get("category"), p.get("barcode"),
                     p.get("store_name"), p.get("street_address"), p.get("city"), p.get("price")]
                    for p in products
                ],
                table_name=_safe_sheet_name(f"{retailer}_pg").replace(" ", "_"),
            )

        # --- Flagged sheet ---
        flags_path = DOCS_DIR / "flags" / f"{retailer}.json"
        if flags_path.exists():
            flags = json.loads(flags_path.read_text(encoding="utf-8"))
            ws = wb.create_sheet(_safe_sheet_name(f"{retailer.title()} Flagged"))
            _write_table(
                ws,
                ["Product", "Brand", "Category", "Store", "City",
                 "Last Seen Price (EUR)", "Days Missing"],
                [
                    [f.get("product"), f.get("brand"), f.get("category"),
                     f.get("store_name"), f.get("city"), f.get("last_seen_price"),
                     f.get("days_missing")]
                    for f in flags
                ],
                table_name=_safe_sheet_name(f"{retailer}_flagged").replace(" ", "_"),
            )
            if flags:
                # Highlight rows missing the longest, most likely genuinely gone.
                max_missing = max(f.get("days_missing", 0) for f in flags)
                for row_idx, f in enumerate(flags, start=2):
                    if f.get("days_missing", 0) >= max(max_missing - 1, 3):
                        for col_idx in range(1, 8):
                            ws.cell(row=row_idx, column=col_idx).fill = WARN_FILL

    _write_table(summary_ws, summary_headers, summary_rows, table_name="SummaryTable")

    # A quick visual comparison across retailers: P&G tracked vs. flagged.
    if summary_rows:
        chart = BarChart()
        chart.title = "P&G products tracked vs. flagged, by retailer"
        chart.y_axis.title = "Count"
        chart.x_axis.title = "Retailer"
        n = len(summary_rows)
        data_ref = Reference(summary_ws, min_col=5, max_col=7, min_row=1, max_row=n + 1)
        cats_ref = Reference(summary_ws, min_col=1, min_row=2, max_row=n + 1)
        chart.add_data(data_ref, titles_from_data=True)
        chart.set_categories(cats_ref)
        chart.width, chart.height = 22, 10
        summary_ws.add_chart(chart, f"A{n + 4}")

    DOCS_DIR.mkdir(parents=True, exist_ok=True)
    wb.save(REPORT_PATH)
    print(f"Wrote {REPORT_PATH}")


if __name__ == "__main__":
    build_report()