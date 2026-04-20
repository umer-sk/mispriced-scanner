# QQQ (Nasdaq-100) holdings — update quarterly
QQQ_TOP50 = [
    # Mega-cap / top 50
    "NVDA", "AAPL", "MSFT", "AMZN", "META",
    "AVGO", "TSLA", "GOOGL", "GOOG", "COST",
    "NFLX", "AMD",  "ADBE", "QCOM", "INTC",
    "AMGN", "CSCO", "TXN",  "INTU", "ISRG",
    "MU",   "AMAT", "LRCX", "MRVL", "KLAC",
    "PLTR", "CRWD", "PANW", "FTNT", "CDNS",
    "PYPL", "MELI", "DXCM", "SNPS", "ABNB",
    "WDAY", "TEAM", "DDOG", "ZS",   "NET",
    "COIN", "DASH", "TTWO", "MNST", "VRSK",
    "ODFL", "KDP",  "EXC",  "AEP",  "CSGP",
    # 51–100
    "TMUS", "CMCSA", "PDD",  "BKNG", "SBUX",
    "GILD", "REGN",  "VRTX", "MDLZ", "ADP",
    "PAYX", "FAST",  "ROST", "PCAR", "EA",
    "CTSH", "BIIB",  "IDXX", "MRNA", "CPRT",
    "CTAS", "CEG",   "ON",   "NXPI", "MCHP",
    "GEHC", "ILMN",  "LULU", "ORLY", "ANSS",
    "CSX",  "WBD",   "HON",  "MAR",  "ROP",
    "FSLR", "TTD",   "PSTG", "ARM",  "DKNG",
    "GFS",  "RBLX",  "LYFT", "MSTR", "ACGL",
]

# Backwards-compatible alias used throughout codebase
QQQ_FULL = QQQ_TOP50

# Map each holding to its SPDR sector ETF
SECTOR_MAP: dict[str, str] = {
    # XLK — Technology
    "NVDA": "XLK", "AAPL": "XLK", "MSFT": "XLK", "AVGO": "XLK", "AMD":  "XLK",
    "ADBE": "XLK", "QCOM": "XLK", "INTC": "XLK", "CSCO": "XLK", "TXN":  "XLK",
    "INTU": "XLK", "MU":   "XLK", "AMAT": "XLK", "LRCX": "XLK", "MRVL": "XLK",
    "KLAC": "XLK", "CDNS": "XLK", "SNPS": "XLK", "PLTR": "XLK", "CRWD": "XLK",
    "PANW": "XLK", "FTNT": "XLK", "ZS":   "XLK", "NET":  "XLK", "DDOG": "XLK",
    "WDAY": "XLK", "TEAM": "XLK", "ON":   "XLK", "NXPI": "XLK", "MCHP": "XLK",
    "CTSH": "XLK", "ANSS": "XLK", "ADP":  "XLK", "PAYX": "XLK", "GFS":  "XLK",
    "TTD":  "XLK", "PSTG": "XLK", "ARM":  "XLK",
    # XLC — Communication Services
    "META":  "XLC", "GOOGL": "XLC", "GOOG": "XLC", "NFLX": "XLC",
    "TTWO":  "XLC", "EA":    "XLC", "CMCSA":"XLC", "TMUS": "XLC",
    "WBD":   "XLC", "RBLX":  "XLC",
    # XLY — Consumer Discretionary
    "AMZN": "XLY", "TSLA": "XLY", "COST": "XLY", "ABNB": "XLY", "MELI": "XLY",
    "DASH": "XLY", "BKNG": "XLY", "SBUX": "XLY", "ROST": "XLY", "LULU": "XLY",
    "ORLY": "XLY", "MAR":  "XLY", "PDD":  "XLY", "DKNG": "XLY", "LYFT": "XLY",
    # XLV — Health Care
    "AMGN": "XLV", "ISRG": "XLV", "DXCM": "XLV", "GILD": "XLV", "REGN": "XLV",
    "VRTX": "XLV", "BIIB": "XLV", "IDXX": "XLV", "MRNA": "XLV", "GEHC": "XLV",
    "ILMN": "XLV",
    # XLF — Financials
    "PYPL": "XLF", "COIN": "XLF", "MSTR": "XLF", "ACGL": "XLF",
    # XLI — Industrials
    "ODFL": "XLI", "VRSK": "XLI", "FAST": "XLI", "PCAR": "XLI", "CTAS": "XLI",
    "CPRT": "XLI", "CSX":  "XLI", "HON":  "XLI", "ROP":  "XLI",
    # XLP — Consumer Staples
    "MNST": "XLP", "KDP": "XLP", "MDLZ": "XLP",
    # XLU — Utilities
    "EXC": "XLU", "AEP": "XLU", "CEG": "XLU",
    # XLRE — Real Estate
    "CSGP": "XLRE",
    # XLE — Energy
    "FSLR": "XLE",
}
