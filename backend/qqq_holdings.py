# Top 50 QQQ holdings by weight — update quarterly
QQQ_TOP50 = [
    "NVDA", "AAPL", "MSFT", "AMZN", "META",
    "AVGO", "TSLA", "GOOGL", "GOOG", "COST",
    "NFLX", "AMD", "ADBE", "QCOM", "INTC",
    "AMGN", "CSCO", "TXN", "INTU", "ISRG",
    "MU",   "AMAT", "LRCX", "MRVL", "KLAC",
    "PLTR", "CRWD", "PANW", "FTNT", "CDNS",
    "PYPL", "MELI", "DXCM", "SNPS", "ABNB",
    "WDAY", "TEAM", "DDOG", "ZS",   "NET",
    "COIN", "DASH", "TTWO", "MNST", "VRSK",
    "ODFL", "KDP",  "EXC",  "AEP",  "CSGP",
]

# Map each holding to its SPDR sector ETF — update quarterly
SECTOR_MAP: dict[str, str] = {
    # XLK — Technology
    "NVDA": "XLK", "AAPL": "XLK", "MSFT": "XLK", "AVGO": "XLK", "AMD": "XLK",
    "ADBE": "XLK", "QCOM": "XLK", "INTC": "XLK", "CSCO": "XLK", "TXN": "XLK",
    "INTU": "XLK", "MU": "XLK",   "AMAT": "XLK", "LRCX": "XLK", "MRVL": "XLK",
    "KLAC": "XLK", "CDNS": "XLK", "SNPS": "XLK", "PLTR": "XLK", "CRWD": "XLK",
    "PANW": "XLK", "FTNT": "XLK", "ZS": "XLK",   "NET": "XLK",  "DDOG": "XLK",
    "WDAY": "XLK", "TEAM": "XLK",
    # XLC — Communication Services
    "META": "XLC", "GOOGL": "XLC", "GOOG": "XLC", "NFLX": "XLC",
    "TTWO": "XLC",
    # XLY — Consumer Discretionary
    "AMZN": "XLY", "TSLA": "XLY", "COST": "XLY", "ABNB": "XLY", "MELI": "XLY",
    "DASH": "XLY",
    # XLV — Health Care
    "AMGN": "XLV", "ISRG": "XLV", "DXCM": "XLV",
    # XLF — Financials
    "PYPL": "XLF", "COIN": "XLF",
    # XLI — Industrials
    "ODFL": "XLI", "VRSK": "XLI",
    # XLP — Consumer Staples
    "MNST": "XLP", "KDP": "XLP",
    # XLU — Utilities
    "EXC": "XLU", "AEP": "XLU",
    # XLRE — Real Estate
    "CSGP": "XLRE",
}

# Backwards-compatibility alias — remove after main.py is updated in Task 8
QQQ_TOP30 = QQQ_TOP50
