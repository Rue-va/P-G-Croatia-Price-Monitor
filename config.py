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
    # Split rather than one merged "Diapers & Wipes" bucket, since Pampers
    # and Violeta both sell diapers AND wipes — a product-name keyword (below)
    # decides which one each row belongs to, rather than one blended count
    # that mixes both product lines together. "Baby Wipes" specifically
    # (not just "Wipes") because Violeta, as a general household brand,
    # also sells makeup-removal wipes, wet toilet paper, and plain dry
    # tissues under names that share the same Croatian word "maramice" —
    # none of which Pampers competes in, so a plain "Wipes" bucket was
    # blending baby wipes with unrelated product lines. Retailer's private
    # label appended to "Baby Wipes" below (the only line we have confirmed
    # evidence it sells; unconfirmed for Diapers).
    "Diapers": ["Pampers", "Violeta"],
    "Baby Wipes": ["Pampers", "Violeta"],
    "Femcare": ["Always", "Naturella", "Libresse", "WeCare", "Carefree"],
    "Hair Care": ["Pantene", "Head & Shoulders", "Herbal Essences", "Elseve", "Garnier", "Gliss", "Syoss", "Schauma"],
    "APDO": ["Old Spice", "Axe", "Rexona", "Nivea", "Dove", "Fa", "Borotalco"],
    "Shave Care": ["Gillette", "BIC", "Wilkinson"],
    "Oral Care": ["Oral-B", "Sensodyne", "Paradontax", "Signal", "Curaprox", "Colgate"],
}

# Some brand names above sell products in more than one watchlist category
# (Violeta makes fabric softener, diapers, AND wipes; Pampers makes both
# diapers and wipes; Jar makes both hand- and machine-dishwashing detergent).
# The brand field alone can't tell these apart, so a product name keyword
# decides which category a row belongs to.
#
# Each category's value is either:
#   - a plain list of keywords: a row counts if the product name contains
#     ANY of them ("include"-only, the original/simple form), or
#   - a dict with "include" and/or "exclude" keys: a row counts if it
#     matches an "include" keyword AND does NOT match any "exclude"
#     keyword. Used where a brand sells several genuinely different product
#     lines under overlapping wording (see "Baby Wipes" below).
# A row that matches neither is left uncounted here (it still counts
# normally everywhere else in the dashboard).
CATEGORY_DISAMBIGUATION_KEYWORDS: dict[str, dict[str, list[str] | dict[str, list[str]]]] = {
    "VIOLETA": {
        "Fabric Enhancers": ["OMEKŠIVA", "OMEKSIVA", "OM "],
        "Diapers": ["PELEN", "PANTS"],
        # Violeta, unlike Pampers, sells several unrelated "maramice"
        # ("wipes"/"tissues" in Croatian) product lines: makeup-removal
        # wipes ("MARAMICE VIOLETA MAKE UP"), wet toilet paper ("TP VLAŽNI
        # VIOLETA NEVEN", "...toal. papir..."), disinfectant wipes, and
        # plain dry tissues ("maramice classic 3 sl.") with no baby
        # indicator at all. Requiring a baby-specific word AND excluding
        # makeup/toilet-paper wording keeps this bucket to genuine baby
        # wet wipes, so it's comparable to Pampers (which only sells baby
        # wipes). Verified against this brand's actual live product names.
        "Baby Wipes": {
            "include": ["BEBI", "BABY", "DJEČ", "DJEC"],
            "exclude": ["MAKE UP", "MAKEUP", "ŠMINK", "SMINK", "DEMAKE", "TOAL"],
        },
    },
    "PAMPERS": {
        "Diapers": ["PELEN", "PANTS"],
        # No exclude list needed — Pampers doesn't sell makeup wipes, wet
        # toilet paper, or dry tissues, so any "wipe"-shaped product name
        # is safely a baby wipe.
        "Baby Wipes": ["MARAMIC", "VLAŽN", "VLAZN", "WIPES"],
    },
    "JAR": {
        "Hand Dishwashing": ["POSUĐ", "POSUD", "SUĐE", "SUDJE"],
        # Retailers abbreviate "tableta" as "TAB" in the product name
        # (e.g. "DET JAR PLATINUM PLUS 40 TAB"), not the full word "TABLETA".
        "Automatic Dishwashing": ["TABLET", "TAB", "KAPSUL", "STROJ", "MAŠIN", "MASIN"],
    },
}

# Some retailers' own brand column already spells out which product line a
# row belongs to, more reliably than any product-name keyword guess could
# (e.g. Konzum's CSV brand field literally reads "JAR HDW" for hand
# dishwashing and "JAR ADW" for automatic dishwashing tablets/capsules).
# When a row's raw brand field matches one of these (after normalizing —
# upper-case, collapsed whitespace), it's assigned straight to that
# category under the given display brand, skipping the CATEGORY_BRANDS /
# CATEGORY_DISAMBIGUATION_KEYWORDS guesswork entirely for that row. Add more
# entries here whenever a retailer's brand field turns out to already encode
# the product line for a brand that sells across multiple watchlist
# categories.
BRAND_FIELD_CATEGORY_OVERRIDES: dict[str, tuple[str, str]] = {
    "JAR HDW": ("Jar", "Hand Dishwashing"),
    "JAR ADW": ("Jar", "Automatic Dishwashing"),
}

# Best-effort store-brand name per retailer, used only for the "Private
# Label" slot in Wipes. We only have a confirmed real name for Konzum
# (K Plus, visible heavily in its own data) — for the others this just
# falls back to the retailer's own name, which is a guess and should be
# replaced once the real private-label brand names are known.
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
