from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Optional


@dataclass
class OptionContract:
    strike: float
    expiry: date
    dte: int
    bid: float
    ask: float
    mid: float
    last: float
    volume: int
    open_interest: int
    iv: float
    delta: float
    gamma: float
    theta: float
    vega: float
    theoretical_value: float
    in_the_money: bool


@dataclass
class OptionChainData:
    symbol: str
    stock_price: float
    iv30: float           # 30-day implied volatility
    hv30: float           # 30-day historical/realized volatility
    iv_rank: float        # 0–100: where current IV sits in 52-week range
    iv_percentile: float  # 0–100: % of days with lower IV in past year
    timestamp: datetime
    calls: list[OptionContract]
    puts: list[OptionContract]
    is_stale: bool = False  # True if data is more than 90 seconds old


@dataclass
class CatalystContext:
    earnings_date: Optional[date]
    earnings_dte: Optional[int]
    earnings_in_window: bool        # True if earnings within trade DTE
    iv_trend: str                   # "RISING" | "FALLING" | "STABLE"
    iv_expansion_likely: bool       # True if earnings approaching AND iv_rank < 30
    recent_volume_spike: bool       # True if today's volume > 1.5x 20-day avg
    catalyst_summary: str           # Human-readable narrative for dashboard


@dataclass
class MispricingSignal:
    symbol: str
    detector: str          # "iv_rank" | "skew" | "parity" | "term" | "move"
    description: str       # One sentence: what is mispriced and why
    confidence: float      # 0.0 – 1.0
    raw_data: dict         # Detector-specific data for debugging


@dataclass
class PnLScenario:
    label: str             # e.g. "Stock +5% in 10 days"
    stock_price: float
    pnl: float             # Dollar P&L per contract (100 shares)
    pnl_pct: float         # Percentage of debit paid


@dataclass
class TradeSetup:
    # Identity
    symbol: str
    stock_price: float
    signal: MispricingSignal
    catalyst: CatalystContext

    # Structure
    structure: str              # "bull_call_spread" | "bear_put_spread" | "calendar" | "long_call"
    long_strike: float
    short_strike: Optional[float]
    expiry: date
    dte: int

    # Economics
    net_debit: float
    max_gain: float
    max_loss: float
    breakeven: float
    breakeven_move_pct: float
    rr_ratio: float
    probability_of_profit: float  # Delta of long strike as proxy

    # Greeks
    net_delta: float
    net_theta: float
    net_vega: float

    # Liquidity
    long_leg_oi: int
    short_leg_oi: int
    long_leg_volume: int
    long_leg_spread_pct: float   # Bid/ask spread as % of mid
    short_leg_spread_pct: float
    liquidity_ok: bool

    # P&L at multiple timeframes (swing trading view)
    scenarios_5d: list[PnLScenario]
    scenarios_10d: list[PnLScenario]
    scenarios_expiry: list[PnLScenario]

    # Scoring
    score: int                   # 0–100
    timestamp: datetime

    # Technical context (populated when yfinance data is available)
    technical_context: Optional["TechnicalContext"] = None
    # Score breakdown for UI transparency
    score_breakdown: list[dict] = field(default_factory=list)

    # Broker order string (copy-paste ready)
    order_string: str


@dataclass
class MarketContext:
    vix_level: float
    vix_trend: str               # "RISING" | "FALLING" | "STABLE"
    market_regime: str           # "RISK_ON" | "RISK_OFF" | "NEUTRAL"
    skip_today: bool
    skip_reason: Optional[str]
    scan_timestamp: datetime
    market_is_open: bool
    next_scan_time: Optional[str]


@dataclass
class TechnicalContext:
    symbol: str
    price: float
    ma50: float
    ma200: float
    pct_from_ma50: float    # (price - ma50) / ma50 * 100
    pct_from_ma200: float   # (price - ma200) / ma200 * 100
    trend: str              # "uptrend" | "downtrend" | "mixed"
    bias: str               # "bullish" | "bearish" | "neutral"


@dataclass
class SectorData:
    etf: str                    # e.g. "XLK"
    name: str                   # e.g. "Technology"
    return_1w: float
    return_4w: float
    return_12w: float
    return_vs_spy_1w: float
    return_vs_spy_4w: float
    return_vs_spy_12w: float
    rs_score: float             # 0–100 relative to other sectors
    trend_direction: str        # "improving" | "deteriorating" | "stable"
    classification: str         # "bullish" | "bearish" | "neutral"
