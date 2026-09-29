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
    "AMBI PUR", "AMBI-PUR", "AMBIPUR", "FEBREZE", "MR. PROPER", "MR PROPER",
    "OLD SPICE", "VENUS", "NATURA SIBERICA",
    "JAR",  # P&G's dishwashing brand (hand + automatic) in Croatia/the Balkans
}

# Manager-curated watchlist: for each P&G "fighting category," the brands
# (P&G's own plus named competitors) worth tracking explicitly. Unlike the
# automatic "top competitor brands" diagnostic (which ranks by raw catalog
# volume and gets crowded out by unrelated grocery brands), every brand here
# is shown regardless of how much volume it does, so a real but lower-volume
# competitor like Persil doesn't disappear. Whether a listed brand counts as
# "P&G" or "competitor" is still decided by PG_BRANDS above, not by where it
# sits in this list, since some categories mix P&G's own family brands
# (Head & Shoulders, Herbal Essences) in with true competitors.
CATEGORY_BRANDS: dict[str, list[str]] = {
    "Laundry": ["Ariel", "Persil", "Dash", "Weisse Riese", "Faks"],
    "Fabric Enhancers": ["Lenor", "Violeta", "Ornel", "Silan"],
    "Hand Dishwashing": ["Jar", "Čarli", "Likvi", "Pur"],
    "Automatic Dishwashing": ["Jar", "Somat", "Finish"],
    "Air Care": ["Ambi Pur", "Glade", "AirWick"],
    "Diapers & Wipes": ["Pampers", "Violeta"],  # retailer's private label appended below
    "Femcare": ["Always", "Naturella", "Libresse", "WeCare", "Carefree"],
    "Hair Care": ["Pantene", "Head & Shoulders", "Herbal Essences", "Elseve", "Garnier", "Gliss", "Syoss", "Schauma"],
    "APDO": ["Old Spice", "Axe", "Rexona", "Nivea", "Dove", "Fa", "Borotalco"],
    "Shave Care": ["Gillette", "BIC", "Wilkinson"],
    "Oral Care": ["Oral-B", "Sensodyne", "Paradontax", "Signal", "Curaprox", "Colgate"],
}

# Some brand names above sell products in more than one watchlist category
# (Violeta makes both fabric softener and diapers/wipes; Jar makes both
# hand- and machine-dishwashing detergent). The brand field alone can't
# tell these apart, so a product name keyword decides which category a row
# belongs to. A row that matches neither keyword set is left uncounted here
# (it still counts normally everywhere else in the dashboard).
CATEGORY_DISAMBIGUATION_KEYWORDS: dict[str, dict[str, list[str]]] = {
    "VIOLETA": {
        "Fabric Enhancers": ["OMEKŠIVA", "OMEKSIVA", "OM "],
        "Diapers & Wipes": ["PELEN", "MARAMIC", "VLAŽN", "VLAZN", "PANTS"],
    },
    "JAR": {
        "Hand Dishwashing": ["POSUĐ", "POSUD", "SUĐE", "SUDJE"],
        "Automatic Dishwashing": ["TABLET", "KAPSUL", "STROJ", "MAŠIN", "MASIN"],
    },
}

# Best-effort store-brand name per retailer, used only for the "Private
# Label" slot in Diapers & Wipes. We only have a confirmed real name for
# Konzum (K Plus, visible heavily in its own data) — for the others this
# just falls back to the retailer's own name, which is a guess and should
# be replaced once the real private-label brand names are known.
PRIVATE_LABEL_BRANDS: dict[str, str] = {
    "konzum": "K Plus",
    "kaufland": "Kaufland",
    "spar": "Spar",
    "lidl": "Lidl",
}

# A P&G product missing from a retailer's daily price list for this many
# consecutive published days is flagged as "likely out of stock". This is an
# inference from public price-list data, not real warehouse inventory data.
MISSING_DAYS_THRESHOLD = 3

# How many days of history to keep informing the dashboard (older history
# files are still kept on disk/in git, just not loaded every run).
LOOKBACK_DAYS = 45