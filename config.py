"""
Shared configuration for the P&G Croatia price/assortment dashboard.
"""
from __future__ import annotations

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
    "NATURELLA", "BLEND-A-MED", "BLEND A MED",
    # Konzum's brand column truncates/annotates names, and its product names
    # abbreviate Head & Shoulders as "H&S" ("ŠAMPON H&S MENTHOL 400ml").
    # Written in normalized form ("&" spaced) since that's what's compared.
    "HEAD & SHOULDE", "H & S", "ALWAYS P & G", "JAR HDW", "JAR ADW",
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
#
# A rule also applies to a brand that sits in only ONE watchlist category
# (e.g. "PUR" below) — there it acts as a gate that keeps unrelated
# products sharing the brand's name out of the category.
_JAR_ADW_KEYWORDS = [
    "TABLET", "TAB", "KAPSUL", "CAPS", "STROJ", "MAŠIN", "MASIN", "PERILIC", "SOL ZA",
]

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
        "Baby Wipes": ["MARAMIC", "VLAŽN", "VLAZN", "WIPES", "VL.MAR", "VL. MAR"],
    },
    "JAR": {
        # Hand dishwashing is "every Jar product that isn't a machine
        # product", not a positive keyword match: retailers abbreviate the
        # liquid wildly ("DET.JAR LEMON 450 ml" at Spar, "Jar ... pos.šipak"
        # or just "Jar Aloe&Pink Jasmin 900ml" at Kaufland), so any include
        # list kept missing real hand-dish SKUs and Spar showed Jar as
        # "not found" in Hand Dishwashing.
        "Hand Dishwashing": {"exclude": _JAR_ADW_KEYWORDS},
        # Retailers abbreviate "tableta" as "TAB" in the product name
        # (e.g. "DET JAR PLATINUM PLUS 40 TAB"), not the full word "TABLETA".
        "Automatic Dishwashing": _JAR_ADW_KEYWORDS,
    },
    # "PUR" is also the standard abbreviation for turkey ("PUR. ŠUNKA",
    # "PIL/PUR MESA"), appears in pet food ("WHIS.PUR.DEL") and inside Spar's
    # "Natur*pur" private label — only count rows that are clearly dish soap.
    "PUR": {
        "Hand Dishwashing": {
            "include": ["DET", "SUĐ", "SUD", "POSU", "PRANJ"],
            "exclude": ["NATUR", "ŠUNK", "SUNK", "MESA", "HRAN", "MAČ", "PAS "],
        },
    },
}

# Retailer catalog categories P&G (and every watchlist competitor) never
# sells in. Product-name matching skips these rows entirely, which removes
# false hits like "LIGNJA JAR" (squid), "VINO VENUS" (wine) or "JAR
# Chardonnay" without having to enumerate each one. Compared upper-cased.
NON_HPC_RETAIL_CATEGORIES = {"HRANA", "PIĆE", "PIĆA", "PICE", "PICA"}

# Canonical display name for each PG_BRANDS spelling, so "ORAL B", "Oral-B"
# and a blank-brand Lidl row titled "Oral-B ..." all roll up to one brand
# in the coverage/promo views. Spellings not listed fall back to title case.
PG_BRAND_DISPLAY = {
    "ORAL-B": "Oral-B", "ORAL B": "Oral-B",
    "HEAD & SHOULDERS": "Head & Shoulders", "HEAD&SHOULDERS": "Head & Shoulders",
    "AMBI PUR": "Ambi Pur", "AMBI-PUR": "Ambi Pur", "AMBIPUR": "Ambi Pur",
    "MR. PROPER": "Mr. Proper", "MR PROPER": "Mr. Proper",
    "HEAD & SHOULDE": "Head & Shoulders", "H & S": "Head & Shoulders",
    "ALWAYS P & G": "Always", "JAR HDW": "Jar", "JAR ADW": "Jar",
    "BLEND-A-MED": "Blend-a-med", "BLEND A MED": "Blend-a-med",
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
    # Konzum: brand field truncated to "HEAD&SHOULDE" / annotated "ALWAYS P&G"
    # (keys are in normalized form — "&" with spaces around it)
    "HEAD & SHOULDE": ("Head & Shoulders", "Hair Care"),
    "ALWAYS P & G": ("Always", "Femcare"),
}

# Manager-decided comparison basis per fighting category, so the price-gap
# numbers compare like-for-like rather than raw shelf price (which rewards
# whoever happens to sell the smaller pack):
#   - "wash":   price per wash/load. Used for laundry detergent and fabric
#     softener, where a concentrated formula can use far fewer ml/g per wash
#     than a diluted one, so €/kg or €/ml alone would mislead. Falls back to
#     the retailer's own published unit price (see "unit" below) whenever a
#     wash-load count can't be read off the product name.
#   - "unit":   the retailer's own published unit price as-is — €/ml, €/kg,
#     €/L or €/piece, whichever applies to that product, exactly as required
#     by Croatia's price-transparency rules (NN 75/2025). This covers both
#     "liquid dishwashers and stuff" (€/ml or €/kg) and "diapers and stuff"
#     (€/piece) with the same mechanism, since the retailer has already done
#     the per-unit math for us on every row.
# Only Laundry and Fabric Enhancers were explicitly called out as "per wash";
# everything else defaults to "unit". Flag to Rue if any other category
# (e.g. Automatic Dishwashing tablets) should also be wash/cycle-based.
CATEGORY_COMPARISON_BASIS: dict[str, str] = {
    "Laundry": "wash",
    "Fabric Enhancers": "wash",
    "Hand Dishwashing": "unit",
    "Automatic Dishwashing": "unit",
    "Air Care": "unit",
    "Diapers": "unit",
    "Baby Wipes": "unit",
    "Femcare": "unit",
    "Hair Care": "unit",
    "APDO": "unit",
    "Shave Care": "unit",
    "Oral Care": "unit",
}

# Regex patterns tried in order against a normalized (upper-cased) product
# name to pull a wash/load count off laundry and fabric-softener packaging.
# Croatian packaging usually states it directly ("ARIEL PRASAK 60 PRANJA",
# "LENOR 40 PRANJA"); imported stock sometimes uses "WL" (wash loads)
# instead. Each pattern's first capture group is the wash count. A product
# that matches neither falls back to the retailer's own €/ml or €/kg unit
# price (CATEGORY_COMPARISON_BASIS above) rather than being dropped.
WASH_COUNT_PATTERNS: list[str] = [
    r"(\d{1,4})\s*X?\s*PRANJ",   # "60 PRANJA", "60X PRANJA", "60PRANJA"
    r"(\d{1,4})\s*WL\b",         # "40 WL", "40WL"
    r"=\s*(\d{1,4})\s*PR\b",      # Konzum: "4.05 L=90PR", "1.8 L=40 PR"
    r"(\d{1,4})\s*\.?\s*PR\b",    # Kaufland: "32pr.", "32.pr."
]

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