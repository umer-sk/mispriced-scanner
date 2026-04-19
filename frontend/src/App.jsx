import { useState, useEffect, useCallback } from 'react'
import { fetchOpportunities, fetchSectorAnalysis } from './api.js'
import Dashboard from './components/Dashboard.jsx'
import TradeJournal from './components/TradeJournal.jsx'
import SectorStrip from './components/SectorStrip.jsx'

const REFRESH_INTERVAL = 5 * 60 * 1000 // 5 minutes

const SECTOR_MAP = {
  NVDA:'XLK', AAPL:'XLK', MSFT:'XLK', AVGO:'XLK', AMD:'XLK',
  ADBE:'XLK', QCOM:'XLK', INTC:'XLK', CSCO:'XLK', TXN:'XLK',
  INTU:'XLK', MU:'XLK', AMAT:'XLK', LRCX:'XLK', MRVL:'XLK',
  KLAC:'XLK', CDNS:'XLK', SNPS:'XLK', PLTR:'XLK', CRWD:'XLK',
  PANW:'XLK', FTNT:'XLK', ZS:'XLK', NET:'XLK', DDOG:'XLK',
  WDAY:'XLK', TEAM:'XLK',
  META:'XLC', GOOGL:'XLC', GOOG:'XLC', NFLX:'XLC', TTWO:'XLC', DASH:'XLC',
  AMZN:'XLY', TSLA:'XLY', COST:'XLY', ABNB:'XLY', MELI:'XLY',
  AMGN:'XLV', ISRG:'XLV', DXCM:'XLV',
  PYPL:'XLF', COIN:'XLF', VRSK:'XLF',
  ODFL:'XLI',
  MNST:'XLP', KDP:'XLP',
  EXC:'XLU', AEP:'XLU',
  CSGP:'XLRE',
}

const styles = {
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
}

export default function App() {
  const [tab, setTab] = useState('dashboard')
  const [data, setData] = useState(null)
  const [error, setError] = useState(null)
  const [loading, setLoading] = useState(true)
  const [sectors, setSectors] = useState([])
  const [activeSector, setActiveSector] = useState(null)
  const [filters, setFilters] = useState({
    minRR: 2.0,
    maxDebit: 8.0,
    minScore: 55,
    detector: 'all',
    direction: 'both',
    minOI: false,
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
          SCANNER
        </button>
        <button
          style={{ ...styles.tab, ...(tab === 'journal' ? styles.tabActive : {}) }}
          onClick={() => setTab('journal')}
        >
          MY TRADES
        </button>
      </div>

      {tab === 'dashboard' && (
        <>
          <SectorStrip
            sectors={sectors}
            activeSector={activeSector}
            onSectorClick={setActiveSector}
          />
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
      {tab === 'journal' && <TradeJournal />}
    </div>
  )
}
