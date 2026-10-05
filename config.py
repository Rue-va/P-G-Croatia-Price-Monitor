"""
Shared configuration for the P&G Croatia price/assortment dashboard.
"""
from __future__ import annotations

from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
HISTORY_DIR = BASE_DIR / "data" / "history"
DOCS_DIR = BASE_DIR / "docs"
DASHBOARD_DATA_FILE = DOCS_DIR / "data.json"


RETAILERS = ["konzum", "kaufland", "spar", "lidl"]


PG_BRANDS = {
    "PAMPERS", "ORAL-B", "ORAL B", "GILLETTE", "HEAD & SHOULDERS",
    "HEAD&SHOULDERS", "PANTENE", "HERBAL ESSENCES", "ARIEL", "TIDE",
    "FAIRY", "LENOR", "VICKS", "OLAY", "BRAUN", "ALWAYS", "DISCREET",
    "AMBI PUR", "AMBI-PUR", "AMBIPUR", "FEBREZE", "MR. PROPER", "MR PROPER",
    "OLD SPICE", "VENUS", "NATURA SIBERICA",
    "JAR", 
    "NATURELLA", "BLEND-A-MED", "BLEND A MED",
  
    "HEAD & SHOULDE", "H & S", "ALWAYS P & G", "JAR HDW", "JAR ADW",
}


CATEGORY_BRANDS: dict[str, list[str]] = {
    "Laundry": ["Ariel", "Persil", "Dash", "Weisse Riese", "Faks"],
    "Fabric Enhancers": ["Lenor", "Violeta", "Ornel", "Silan"],
    "Hand Dishwashing": ["Jar", "Čarli", "Likvi", "Pur"],
    "Automatic Dishwashing": ["Jar", "Somat", "Finish"],
    "Air Care": ["Ambi Pur", "Glade", "AirWick"],

    "Diapers": ["Pampers", "Violeta"],
    "Baby Wipes": ["Pampers", "Violeta"],
    "Femcare": ["Always", "Naturella", "Libresse", "WeCare", "Carefree"],
    "Hair Care": ["Pantene", "Head & Shoulders", "Herbal Essences", "Elseve", "Garnier", "Gliss", "Syoss", "Schauma"],
    "APDO": ["Old Spice", "Axe", "Rexona", "Nivea", "Dove", "Fa", "Borotalco"],
    "Shave Care": ["Gillette", "BIC", "Wilkinson"],
    "Oral Care": ["Oral-B", "Sensodyne", "Paradontax", "Signal", "Curaprox", "Colgate"],
}



_JAR_ADW_KEYWORDS = [
    "TABLET", "TAB", "KAPSUL", "CAPS", "STROJ", "MAŠIN", "MASIN", "PERILIC", "SOL ZA",
]

CATEGORY_DISAMBIGUATION_KEYWORDS: dict[str, dict[str, list[str] | dict[str, list[str]]]] = {
    "VIOLETA": {
        "Fabric Enhancers": ["OMEKŠIVA", "OMEKSIVA", "OM "],
        "Diapers": ["PELEN", "PANTS"],
 
        "Baby Wipes": {
            "include": ["BEBI", "BABY", "DJEČ", "DJEC"],
            "exclude": ["MAKE UP", "MAKEUP", "ŠMINK", "SMINK", "DEMAKE", "TOAL"],
        },
    },
    "PAMPERS": {
        "Diapers": ["PELEN", "PANTS"],
      
        "Baby Wipes": ["MARAMIC", "VLAŽN", "VLAZN", "WIPES", "VL.MAR", "VL. MAR"],
    },
    "JAR": {
       
        "Hand Dishwashing": {"exclude": _JAR_ADW_KEYWORDS},
     
        "Automatic Dishwashing": _JAR_ADW_KEYWORDS,
    },

    "PUR": {
        "Hand Dishwashing": {
            "include": ["DET", "SUĐ", "SUD", "POSU", "PRANJ"],
            "exclude": ["NATUR", "ŠUNK", "SUNK", "MESA", "HRAN", "MAČ", "PAS "],
        },
    },
}


NON_HPC_RETAIL_CATEGORIES = {"HRANA", "PIĆE", "PIĆA", "PICE", "PICA"}


PG_BRAND_DISPLAY = {
    "ORAL-B": "Oral-B", "ORAL B": "Oral-B",
    "HEAD & SHOULDERS": "Head & Shoulders", "HEAD&SHOULDERS": "Head & Shoulders",
    "AMBI PUR": "Ambi Pur", "AMBI-PUR": "Ambi Pur", "AMBIPUR": "Ambi Pur",
    "MR. PROPER": "Mr. Proper", "MR PROPER": "Mr. Proper",
    "HEAD & SHOULDE": "Head & Shoulders", "H & S": "Head & Shoulders",
    "ALWAYS P & G": "Always", "JAR HDW": "Jar", "JAR ADW": "Jar",
    "BLEND-A-MED": "Blend-a-med", "BLEND A MED": "Blend-a-med",
}


BRAND_FIELD_CATEGORY_OVERRIDES: dict[str, tuple[str, str]] = {
    "JAR HDW": ("Jar", "Hand Dishwashing"),
    "JAR ADW": ("Jar", "Automatic Dishwashing"),

    "HEAD & SHOULDE": ("Head & Shoulders", "Hair Care"),
    "ALWAYS P & G": ("Always", "Femcare"),
}


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


WASH_COUNT_PATTERNS: list[str] = [
    r"(\d{1,4})\s*X?\s*PRANJ",   
    r"(\d{1,4})\s*WL\b",        
    r"=\s*(\d{1,4})\s*PR\b",     
    r"(\d{1,4})\s*\.?\s*PR\b",    
]


PRIVATE_LABEL_BRANDS: dict[str, str] = {
    "konzum": "K Plus",
    "kaufland": "Kaufland",
    "spar": "Spar",
    "lidl": "Lidl",
}


MISSING_DAYS_THRESHOLD = 3


LOOKBACK_DAYS = 45
