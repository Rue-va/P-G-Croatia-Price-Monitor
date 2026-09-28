"""
Offline smoke test: builds synthetic Store/Product objects (mimicking what a
real crawler would return) and runs them through build_day_payload ->
save_day -> compute_missing_flags -> the same aggregation the export script
does, without touching the network. This is meant to catch structural bugs
before the first real run against live retailer sites (which aren't
reachable from this dev sandbox).
"""
import shutil
import sys
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from crawler_vendor.models import Product, Store
import config
import history


def make_store(store_id, pg_present=True):
    items = [
        Product(
            product="Pampers Pants vel. 4", product_id="P001", brand="PAMPERS",
            quantity="52 kom", unit="kom", price=Decimal("24.99"),
            unit_price=Decimal("0.48"), barcode="1111", category="Pelene",
        ),
        Product(
            product="Ariel gel 40 pranja", product_id="P002", brand="ARIEL",
            quantity="2 l", unit="l", price=Decimal("12.49"),
            unit_price=Decimal("6.25"), barcode="2222", category="Deterdzenti",
        ),
        Product(
            product="Competitor detergent", product_id="C001", brand="OTHERBRAND",
            quantity="2 l", unit="l", price=Decimal("9.99"),
            unit_price=Decimal("5.00"), barcode="3333", category="Deterdzenti",
        ),
    ]
    if not pg_present:
        items = [i for i in items if i.brand != "ARIEL"]
    return Store(
        chain="testretailer", store_id=store_id, name=f"Test Store {store_id}",
        store_type="supermarket", city="Zagreb", street_address="Test 1",
        zipcode="10000", items=items,
    )


def run():
    tmp_hist = Path(__file__).resolve().parent / "_tmp_history"
    if tmp_hist.exists():
        shutil.rmtree(tmp_hist)
    config.HISTORY_DIR = tmp_hist
    history.HISTORY_DIR = tmp_hist  # module-level constant already imported by value; patch directly
    import importlib
    importlib.reload(history)
    history.HISTORY_DIR = tmp_hist

    retailer = "testretailer"
    today = date(2026, 9, 28)

    # Day -4 through day -1: Ariel present. Day 0 (today): Ariel missing at
    # store S1 (should NOT flag yet, only 1 day missing) but store S2 has it
    # missing for the last 3 days already (should flag).
    for offset in range(4, 0, -1):
        d = today - timedelta(days=offset)
        stores = [make_store("S1", pg_present=True), make_store("S2", pg_present=True)]
        payload = history.build_day_payload(retailer, d, stores)
        history.save_day(retailer, d, payload)

    # Day -3, -2, -1, 0: S2 loses Ariel (3 consecutive missing days as of today)
    for offset, d in [(3, today - timedelta(days=3)), (2, today - timedelta(days=2)),
                       (1, today - timedelta(days=1)), (0, today)]:
        stores = [make_store("S1", pg_present=True), make_store("S2", pg_present=(offset == 0 and False) or False)]
        # S1 always present; S2 missing Ariel starting 3 days ago through today
        stores = [make_store("S1", pg_present=True), make_store("S2", pg_present=False)]
        payload = history.build_day_payload(retailer, d, stores)
        history.save_day(retailer, d, payload)

    flags = history.compute_missing_flags(retailer, today, threshold=3)
    print(f"Flags found: {len(flags)}")
    for f in flags:
        print(f"  {f['product']} @ {f['store_name']} — missing {f['days_missing']} days")

    assert len(flags) == 1, f"expected 1 flagged item, got {len(flags)}"
    assert flags[0]["store_id"] == "S2"
    assert flags[0]["product_id"] == "P002"
    assert flags[0]["days_missing"] >= 3

    # category stats sanity check
    latest = history.load_day(retailer, today)
    assert latest["category_stats"]["Deterdzenti"]["pg_count"] >= 1
    # S1 has all 3 items, S2 has Ariel removed (2 items) -> 5 total
    assert latest["total_products_all_stores"] == 5
    print("total_products_all_stores:", latest["total_products_all_stores"])

    shutil.rmtree(tmp_hist)
    print("\nSMOKE TEST PASSED")


if __name__ == "__main__":
    run()
