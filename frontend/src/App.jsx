import { useState, useEffect, useCallback } from 'react'
import { fetchOpportunities, fetchSectorAnalysis } from './api.js'
import Dashboard from './components/Dashboard.jsx'
import TradeJournal from './components/TradeJournal.jsx'
import SectorStrip from './components/SectorStrip.jsx'
import TechnicalSetups from './components/TechnicalSetups.jsx'
import CeltSetups from './components/CeltSetups.jsx'
import ForwardTest from './components/ForwardTest.jsx'

const REFRESH_INTERVAL = 5 * 60 * 1000 // 5 minutes

// Symbol -> sector ETF. MUST cover every symbol in backend/qqq_holdings.py:
// an unmapped symbol resolves to undefined and is filtered out of EVERY sector,
// so it silently becomes unreachable through the sector UI. This map had 50
// entries against 93 holdings, hiding 43 symbols. See the unmapped-count
// warning below, which makes any future drift visible instead of silent —
// holdings are updated quarterly and this map will drift again.
const SECTOR_MAP = {
  // Technology
  NVDA:'XLK', AAPL:'XLK', MSFT:'XLK', AVGO:'XLK', AMD:'XLK',
  ADBE:'XLK', QCOM:'XLK', INTC:'XLK', CSCO:'XLK', TXN:'XLK',
  INTU:'XLK', MU:'XLK', AMAT:'XLK', LRCX:'XLK', MRVL:'XLK',
  KLAC:'XLK', CDNS:'XLK', SNPS:'XLK', PLTR:'XLK', CRWD:'XLK',
  PANW:'XLK', FTNT:'XLK', ZS:'XLK', NET:'XLK', DDOG:'XLK',
  WDAY:'XLK', TEAM:'XLK', ON:'XLK', NXPI:'XLK', MCHP:'XLK',
  ARM:'XLK', GFS:'XLK', CTSH:'XLK', ROP:'XLK', FSLR:'XLK',
  TTD:'XLK', MSTR:'XLK',
  // Communication Services
  META:'XLC', GOOGL:'XLC', GOOG:'XLC', NFLX:'XLC', TTWO:'XLC', DASH:'XLC',
  TMUS:'XLC', CMCSA:'XLC', EA:'XLC', WBD:'XLC', RBLX:'XLC',
  // Consumer Discretionary
  AMZN:'XLY', TSLA:'XLY', COST:'XLY', ABNB:'XLY', MELI:'XLY',
  PDD:'XLY', BKNG:'XLY', SBUX:'XLY', ROST:'XLY', LULU:'XLY',
  ORLY:'XLY', MAR:'XLY', DKNG:'XLY',
  // Health Care
  AMGN:'XLV', ISRG:'XLV', DXCM:'XLV', GILD:'XLV', REGN:'XLV',
  VRTX:'XLV', BIIB:'XLV', IDXX:'XLV', MRNA:'XLV', GEHC:'XLV',
  ILMN:'XLV',
  // Financials
  PYPL:'XLF', COIN:'XLF', VRSK:'XLF', ACGL:'XLF',
  // Industrials
  ODFL:'XLI', FAST:'XLI', PCAR:'XLI', CPRT:'XLI', CTAS:'XLI',
  CSX:'XLI', HON:'XLI', ADP:'XLI', PAYX:'XLI', LYFT:'XLI',
  // Consumer Staples
  MNST:'XLP', KDP:'XLP', MDLZ:'XLP',
  // Utilities
  EXC:'XLU', AEP:'XLU', CEG:'XLU',
  // Real Estate
  CSGP:'XLRE',
}

const styles = {
  unmappedWarning: {
    margin: '0 16px 8px',
    padding: '8px 12px',
    background: '#2a1f00',
    border: '1px solid #665200',
    borderRadius: '4px',
    color: '#ffaa00',
    fontFamily: 'monospace',
    fontSize: '11px',
  },
  tabBar: {
    display: 'flex',
    gap: '2px',
    padding: '12px 16px 0',
    borderBottom: '1px solid #1a1a2e',
    background: '#0a0a14',
  },
  tab: {
    padding: '8px 20px',
    cursor: 'pointer',
    border: 'none',
    background: 'none',
    color: '#666',
    fontFamily: 'monospace',
    fontSize: '13px',
    letterSpacing: '0.05em',
    borderBottom: '2px solid transparent',
  },
  tabActive: {
    color: '#00ffaa',
    borderBottom: '2px solid #00ffaa',
  },
  tabDescription: {
    padding: '8px 16px',
    borderBottom: '1px solid #1a1a2e',
    background: '#07070f',
    color: '#666',
    fontFamily: 'monospace',
    fontSize: '11px',
    lineHeight: '1.5',
  },
}

const TAB_DESCRIPTIONS = {
  dashboard: 'Compares option prices to a fair-value model across 9 signals (IV rank, skew, put-call parity, term structure, straddle vs. realized move) to find contracts priced away from where the math says they should be.',
  setups: 'Scores each stock on 7 technical signals (trend stage, EMA alignment, RSI, volume, relative strength, breakout) plus a separate 200-week MA bounce detector, and builds a call/put or spread around whichever signals agree.',
  celt: 'Flags QQQ holdings in a genuine drawdown — price damage, volatility spike, sentiment capitulation — and pairs each with a deep-ITM LEAP call as a low-cost way to buy the recovery.',
  forward: 'Every setup the scanner surfaces gets logged and marked to market against fixed exit rules (+50%/+100% targets, -50% stop) — what actually happened, not a backtest.',
}

export default function App() {
  const [tab, setTab] = useState('dashboard')
  const [data, setData] = useState(null)
  const [error, setError] = useState(null)
  const [loading, setLoading] = useState(true)
  const [sectors, setSectors] = useState([])
  const [activeSector, setActiveSector] = useState(null)
  const [filters, setFilters] = useState({
    detector: 'all',
    direction: 'both',
    sort: 'score',
  })

  const load = useCallback(async () => {
    try {
      const result = await fetchOpportunities(filters)
      setData(result)
      setError(null)
    } catch (err) {
      setError(err.message)
    } finally {
      setLoading(false)
    }
  }, [filters])

  useEffect(() => {
    load()
    const interval = setInterval(load, REFRESH_INTERVAL)
    return () => clearInterval(interval)
  }, [load])

  useEffect(() => {
    fetchSectorAnalysis()
      .then(d => setSectors(d.sectors || []))
      .catch(err => console.warn('Sector fetch failed:', err))
  }, [])

  const opportunities = data?.opportunities ?? []
  // Any opportunity whose symbol is missing from SECTOR_MAP would vanish from
  // every sector without trace. Count them so drift surfaces instead of
  // silently hiding setups.
  const unmapped = opportunities.filter(o => !SECTOR_MAP[o.symbol])
  const visibleOpps = activeSector
    ? opportunities.filter(o => SECTOR_MAP[o.symbol] === activeSector)
    : opportunities
  const filteredData = data ? { ...data, opportunities: visibleOpps } : data

  return (
    <div style={{ minHeight: '100vh', background: '#030308' }}>
      <div style={styles.tabBar}>
        <button
          style={{ ...styles.tab, ...(tab === 'dashboard' ? styles.tabActive : {}) }}
          onClick={() => setTab('dashboard')}
        >
          MISPRICED OPTIONS
        </button>
        <button
          style={{ ...styles.tab, ...(tab === 'setups' ? styles.tabActive : {}) }}
          onClick={() => setTab('setups')}
        >
          TECHNICAL SETUPS
        </button>
        <button
          style={{ ...styles.tab, ...(tab === 'celt' ? styles.tabActive : {}) }}
          onClick={() => setTab('celt')}
        >
          CRASH ENTRY LEAPS
        </button>
        <button
          style={{ ...styles.tab, ...(tab === 'journal' ? styles.tabActive : {}) }}
          onClick={() => setTab('journal')}
        >
          MY TRADES
        </button>
        <button
          style={{ ...styles.tab, ...(tab === 'forward' ? styles.tabActive : {}) }}
          onClick={() => setTab('forward')}
        >
          FORWARD TEST
        </button>
      </div>

      {TAB_DESCRIPTIONS[tab] && (
        <div style={styles.tabDescription}>{TAB_DESCRIPTIONS[tab]}</div>
      )}

      {tab === 'dashboard' && (
        <>
          <SectorStrip
            sectors={sectors}
            activeSector={activeSector}
            onSectorClick={setActiveSector}
          />
          {activeSector && unmapped.length > 0 && (
            <div style={styles.unmappedWarning}>
              ⚠ {unmapped.length} setup{unmapped.length > 1 ? 's are' : ' is'} hidden by this
              filter because {unmapped.length > 1 ? 'their symbols are' : 'its symbol is'} not
              in SECTOR_MAP ({unmapped.map(o => o.symbol).join(', ')}).
              Holdings change quarterly — add {unmapped.length > 1 ? 'them' : 'it'} in App.jsx.
            </div>
          )}
          <Dashboard
            data={filteredData}
            loading={loading}
            error={error}
            filters={filters}
            onFiltersChange={setFilters}
            onRefresh={load}
          />
        </>
      )}
      {tab === 'setups' && <TechnicalSetups />}
      {tab === 'celt' && <CeltSetups />}
      {tab === 'journal' && <TradeJournal />}
      {tab === 'forward' && <ForwardTest />}
    </div>
  )
}
