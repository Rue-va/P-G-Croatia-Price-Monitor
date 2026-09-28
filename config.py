"""
Shared configuration for the P&G Croatia price/assortment dashboard.
"""
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
HISTORY_DIR = BASE_DIR / "data" / "history"
DOCS_DIR = BASE_DIR / "docs"
DASHBOARD_DATA_FILE = DOCS_DIR / "data.json"

# Retailers we crawl. Each key must match a vendored crawler class in
# crawler_vendor/ (ClassName = key.capitalize() + "Crawler").
RETAILERS = ["konzum", "kaufland", "spar", "lidl"]

# Brand names (as they appear, upper-cased, in retailer CSVs) that count as
# P&G for this tool. Extend this list if a report shows a P&G product being
# missed under a brand spelling not listed here.
PG_BRANDS = {
    "PAMPERS", "ORAL-B", "ORAL B", "GILLETTE", "HEAD & SHOULDERS",
    "HEAD&SHOULDERS", "PANTENE", "HERBAL ESSENCES", "ARIEL", "TIDE",
    "FAIRY", "LENOR", "VICKS", "OLAY", "BRAUN", "ALWAYS", "DISCREET",
    "AMBI PUR", "AMBI-PUR", "FEBREZE", "MR. PROPER", "MR PROPER",
    "OLD SPICE", "VENUS", "NATURA SIBERICA",
}

# A P&G product missing from a retailer's daily price list for this many
# consecutive published days is flagged as "likely out of stock". This is an
# inference from public price-list data, not real warehouse inventory data.
MISSING_DAYS_THRESHOLD = 3

# How many days of history to keep informing the dashboard (older history
# files are still kept on disk/in git, just not loaded every run).
LOOKBACK_DAYS = 45
