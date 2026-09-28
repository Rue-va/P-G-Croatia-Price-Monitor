"""
Integration smoke test for scrape_and_export.run_retailer(), using a fake
crawler (network-free) to catch wiring bugs between history.py and
scrape_and_export.py before the real GitHub Actions run hits live sites.
"""
import json
import shutil
import sys
from datetime import date
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import config

tmp_root = Path(__file__).resolve().parent / "_tmp_integration"
if tmp_root.exists():
    shutil.rmtree(tmp_root)
config.HISTORY_DIR = tmp_root / "history"
config.DOCS_DIR = tmp_root / "docs"
config.DASHBOARD_DATA_FILE = config.DOCS_DIR / "data.json"

import importlib
import history
importlib.reload(history)
history.HISTORY_DIR = config.HISTORY_DIR

import scrape_and_export
importlib.reload(scrape_and_export)
scrape_and_export.DASHBOARD_DATA_FILE = config.DASHBOARD_DATA_FILE
scrape_and_export.DOCS_DIR = config.DOCS_DIR

from crawler_vendor.models import Product, Store


class FakeCrawler:
    def get_all_products(self, target_date):
        return [
            Store(
                chain="fake", store_id="F1", name="Fake Store 1", store_type="supermarket",
                city="Zagreb", street_address="X 1", zipcode="10000",
                items=[
                    Product(product="Gillette Fusion", product_id="G1", brand="GILLETTE",
                            quantity="1 kom", unit="kom", price=Decimal("15.99"),
                            unit_price=Decimal("15.99"), barcode="9999", category="Brijanje"),
                ],
            )
        ]


scrape_and_export.get_crawler = lambda retailer: FakeCrawler()

result = scrape_and_export.run_retailer("fakeretailer", date(2026, 9, 28))
print(json.dumps(result, indent=1, ensure_ascii=False)[:600])
assert result["status"] == "ok"
assert result["pg_products_tracked"] == 1
assert result["total_catalog_size"] == 1
assert "Brijanje" in result["category_stats"]

# Now run the full main() path and check docs/data.json + docs/flags/*.json exist
scrape_and_export.RETAILERS = ["fakeretailer"]
sys.argv = ["scrape_and_export.py", "--date", "2026-09-28", "--retailers", "fakeretailer"]
scrape_and_export.main()

data_json = json.loads(config.DASHBOARD_DATA_FILE.read_text())
assert data_json["retailers"][0]["retailer"] == "fakeretailer"
assert (config.DOCS_DIR / "flags" / "fakeretailer.json").exists()

shutil.rmtree(tmp_root)
print("\nINTEGRATION TEST PASSED")
