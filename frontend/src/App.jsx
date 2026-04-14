import { useState, useEffect, useCallback } from 'react'
import { fetchOpportunities } from './api.js'
import Dashboard from './components/Dashboard.jsx'
import TradeJournal from './components/TradeJournal.jsx'

const REFRESH_INTERVAL = 5 * 60 * 1000 // 5 minutes

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
  const [filters, setFilters] = useState({
    minRR: 2.0,
    maxDebit: 8.0,
    minScore: 55,
    detector: 'all',
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
        <Dashboard
          data={data}
          loading={loading}
          error={error}
          filters={filters}
          onFiltersChange={setFilters}
          onRefresh={load}
        />
      )}
      {tab === 'journal' && <TradeJournal />}
    </div>
  )
}
