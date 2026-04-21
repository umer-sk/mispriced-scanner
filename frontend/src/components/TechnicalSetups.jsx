import { useState, useEffect } from 'react'
import { fetchTechnicalSetups, triggerSetupsScan } from '../api.js'

const SIGNAL_LABELS = {
  price_vs_ema21: 'Price>21EMA',
  ema_alignment:  '13/21 EMA',
  stage2:         'Stage 2',
  rsi_zone:       'RSI',
  volume_accum:   'Volume',
  rs_vs_qqq:      'RS vs QQQ',
  breakout:       'Near High',
}

function SignalBadges({ details, direction }) {
  return (
    <div style={styles.signals}>
      {Object.entries(SIGNAL_LABELS).map(([key, label]) => {
        const isBullishSignal = details[key]
        const firing = direction === 'bullish' ? isBullishSignal : !isBullishSignal
        return (
          <span key={key} style={{ ...styles.signal, color: firing ? '#00ffaa' : '#333' }}>
            {firing ? '✓' : '·'} {label}
          </span>
        )
      })}
    </div>
  )
}

function SetupCard({ setup }) {
  const [copied, setCopied] = useState(false)
  const isBearish = setup.direction === 'bearish'
  const structureLabel = {
    long_call:       'Long Call',
    long_put:        'Long Put',
    bull_call_spread:'Bull Call Spread',
    bear_put_spread: 'Bear Put Spread',
  }[setup.structure] ?? setup.structure

  const expiryStr = setup.expiry
    ? new Date(setup.expiry + 'T00:00:00').toLocaleDateString('en-US', { month: 'short', day: 'numeric' })
    : '—'

  function copyOrder() {
    navigator.clipboard.writeText(setup.order_string).then(() => {
      setCopied(true)
      setTimeout(() => setCopied(false), 2000)
    })
  }

  return (
    <div style={{ ...styles.card, borderLeft: `3px solid ${isBearish ? '#ff4444' : '#00ffaa'}` }}>
      <div style={styles.cardHeader}>
        <div style={styles.cardLeft}>
          <span style={styles.symbol}>{setup.symbol}</span>
          <span style={styles.price}>${setup.stock_price?.toFixed(2)}</span>
        </div>
        <div style={styles.cardRight}>
          <span style={{ ...styles.badge, color: isBearish ? '#ff4444' : '#00ffaa', borderColor: isBearish ? '#ff4444' : '#00ffaa' }}>
            {setup.signal_count}/7 {setup.direction.toUpperCase()}
          </span>
          <span style={styles.structureLabel}>{structureLabel}</span>
        </div>
      </div>

      <div style={styles.meta}>
        <span>{expiryStr}</span>
        <span>${setup.strike?.toFixed(0)}{setup.short_strike ? `/${setup.short_strike?.toFixed(0)}` : ''}</span>
        <span>{setup.dte} DTE</span>
        <span>Δ{Math.abs(setup.delta)?.toFixed(2)}</span>
        <span style={{ color: setup.iv_rank > 65 ? '#ffaa00' : '#666' }}>IV Rank {setup.iv_rank?.toFixed(0)}</span>
      </div>

      <div style={styles.metrics}>
        <div style={styles.metric}>
          <span style={styles.metricLabel}>R:R</span>
          <span style={{ ...styles.metricVal, color: '#00ffaa' }}>{setup.rr_ratio?.toFixed(1)}:1</span>
        </div>
        <div style={styles.metric}>
          <span style={styles.metricLabel}>Max Loss</span>
          <span style={styles.metricVal}>${setup.max_loss?.toFixed(0)}</span>
        </div>
        <div style={styles.metric}>
          <span style={styles.metricLabel}>Breakeven</span>
          <span style={styles.metricVal}>{setup.breakeven_move_pct > 0 ? '+' : ''}{setup.breakeven_move_pct?.toFixed(1)}%</span>
        </div>
        <div style={styles.metric}>
          <span style={styles.metricLabel}>PoP</span>
          <span style={styles.metricVal}>{setup.probability_of_profit}%</span>
        </div>
        <div style={styles.metric}>
          <span style={styles.metricLabel}>Target</span>
          <span style={styles.metricVal}>${setup.price_target?.toFixed(0)}</span>
        </div>
      </div>

      <SignalBadges details={setup.signal_details} direction={setup.direction} />

      <button style={styles.copyBtn} onClick={copyOrder}>
        {copied ? '✓ Copied' : 'Copy Order'}
      </button>
    </div>
  )
}

export default function TechnicalSetups() {
  const [setups, setSetups] = useState([])
  const [loading, setLoading] = useState(true)
  const [scanning, setScanning] = useState(false)
  const [error, setError] = useState(null)
  const [scanTimestamp, setScanTimestamp] = useState(null)
  const [filters, setFilters] = useState({ direction: 'both', minRR: 2.0, sort: 'rr' })

  useEffect(() => {
    load()
  }, [filters])

  async function load() {
    try {
      const data = await fetchTechnicalSetups(filters)
      setSetups(data.setups || [])
      setScanTimestamp(data.scan_timestamp)
      setError(null)
    } catch (e) {
      setError(e.message)
    } finally {
      setLoading(false)
    }
  }

  async function runScan() {
    setScanning(true)
    try {
      await triggerSetupsScan()
      // Poll after 45s for results
      setTimeout(async () => {
        await load()
        setScanning(false)
      }, 45000)
    } catch (e) {
      setError(e.message)
      setScanning(false)
    }
  }

  const scanTime = scanTimestamp
    ? new Date(scanTimestamp).toLocaleTimeString('en-US', { hour: '2-digit', minute: '2-digit', timeZone: 'America/New_York' })
    : '—'

  return (
    <div>
      {/* Header */}
      <div style={styles.header}>
        <div>
          <span style={styles.title}>TECHNICAL SETUPS</span>
          <span style={styles.count}>{loading ? '…' : `${setups.length} setups`}</span>
        </div>
        <div style={styles.headerRight}>
          {scanTimestamp && (
            <span style={styles.scanTime}>Last scan: {scanTime}</span>
          )}
          <button
            style={{ ...styles.scanBtn, ...(scanning ? styles.scanBtnActive : {}) }}
            onClick={runScan}
            disabled={scanning}
          >
            {scanning ? '⟳ SCANNING…' : '▶ SCAN SETUPS'}
          </button>
        </div>
      </div>

      {scanning && (
        <div style={styles.scanningBanner}>
          ⟳ Scanning 100 symbols — takes ~45 seconds. Results will load automatically.
        </div>
      )}

      {error && (
        <div style={styles.errorBanner}>Could not fetch setups: {error}</div>
      )}

      {/* Filters */}
      <div style={styles.filterBar}>
        <div style={styles.filterGroup}>
          <span style={styles.filterLabel}>DIRECTION</span>
          {['both', 'bullish', 'bearish'].map(d => (
            <button
              key={d}
              style={{ ...styles.filterBtn, ...(filters.direction === d ? styles.filterBtnActive : {}) }}
              onClick={() => setFilters(f => ({ ...f, direction: d }))}
            >
              {d === 'both' ? 'Both' : d === 'bullish' ? '▲ Bullish' : '▼ Bearish'}
            </button>
          ))}
        </div>
        <div style={styles.filterGroup}>
          <span style={styles.filterLabel}>MIN R:R</span>
          <input
            type="range" min="1.0" max="5.0" step="0.5"
            value={filters.minRR}
            onChange={e => setFilters(f => ({ ...f, minRR: parseFloat(e.target.value) }))}
            style={styles.slider}
          />
          <span style={styles.filterVal}>{filters.minRR.toFixed(1)}:1</span>
        </div>
        <div style={styles.filterGroup}>
          <span style={styles.filterLabel}>SORT</span>
          <button
            style={{ ...styles.filterBtn, ...(filters.sort === 'rr' ? styles.filterBtnActive : {}) }}
            onClick={() => setFilters(f => ({ ...f, sort: 'rr' }))}
          >R:R</button>
          <button
            style={{ ...styles.filterBtn, ...(filters.sort === 'pop' ? styles.filterBtnActive : {}) }}
            onClick={() => setFilters(f => ({ ...f, sort: 'pop' }))}
          >PoP</button>
        </div>
      </div>

      {/* Results */}
      {!loading && setups.length === 0 && !scanning && (
        <div style={styles.empty}>
          {scanTimestamp
            ? 'No setups meet your filters. Try lowering Min R:R or running a fresh scan.'
            : 'No scan data yet. Click ▶ SCAN SETUPS to run the first scan.'}
        </div>
      )}

      <div style={{ paddingBottom: '32px' }}>
        {setups.map((setup, i) => (
          <SetupCard key={`${setup.symbol}-${setup.structure}-${i}`} setup={setup} />
        ))}
      </div>
    </div>
  )
}

const styles = {
  header: {
    display: 'flex', justifyContent: 'space-between', alignItems: 'center',
    padding: '16px', borderBottom: '1px solid #1a1a2e',
    background: '#0a0a14', flexWrap: 'wrap', gap: '8px',
  },
  title: {
    fontFamily: 'monospace', fontSize: '16px', fontWeight: 'bold',
    color: '#00ffaa', marginRight: '16px', letterSpacing: '0.05em',
  },
  count: { fontFamily: 'monospace', fontSize: '13px', color: '#666' },
  headerRight: { display: 'flex', alignItems: 'center', gap: '12px' },
  scanTime: { fontFamily: 'monospace', fontSize: '12px', color: '#555' },
  scanBtn: {
    background: 'none', border: '1px solid #00ffaa', color: '#00ffaa',
    cursor: 'pointer', padding: '4px 12px', borderRadius: '3px',
    fontFamily: 'monospace', fontSize: '11px', letterSpacing: '0.05em',
  },
  scanBtnActive: { color: '#555', borderColor: '#333', cursor: 'not-allowed' },
  scanningBanner: {
    background: '#0a0a14', borderLeft: '4px solid #00ffaa',
    color: '#00ffaa', padding: '8px 16px', fontSize: '12px', fontFamily: 'monospace',
  },
  errorBanner: {
    background: '#1a0505', borderLeft: '4px solid #ff4444',
    color: '#ff4444', padding: '8px 16px', fontSize: '12px', fontFamily: 'monospace',
  },
  filterBar: {
    display: 'flex', gap: '24px', padding: '10px 16px',
    borderBottom: '1px solid #1a1a2e', flexWrap: 'wrap', alignItems: 'center',
  },
  filterGroup: { display: 'flex', alignItems: 'center', gap: '6px' },
  filterLabel: { fontFamily: 'monospace', fontSize: '10px', color: '#555', letterSpacing: '0.08em' },
  filterBtn: {
    padding: '3px 10px', background: 'none', border: '1px solid #2a2a3e',
    color: '#666', cursor: 'pointer', fontFamily: 'monospace', fontSize: '11px', borderRadius: '3px',
  },
  filterBtnActive: { borderColor: '#00ffaa', color: '#00ffaa', background: '#0a1a0f' },
  slider: { width: '80px', accentColor: '#00ffaa' },
  filterVal: { fontFamily: 'monospace', fontSize: '11px', color: '#aaa', minWidth: '32px' },
  empty: {
    padding: '48px 16px', textAlign: 'center',
    color: '#555', fontFamily: 'monospace', fontSize: '13px',
  },
  card: {
    background: '#0d0d1a', border: '1px solid #1a1a2e',
    borderRadius: '4px', padding: '12px 16px', margin: '8px 16px',
  },
  cardHeader: {
    display: 'flex', justifyContent: 'space-between',
    alignItems: 'center', marginBottom: '6px', flexWrap: 'wrap', gap: '8px',
  },
  cardLeft: { display: 'flex', alignItems: 'baseline', gap: '10px' },
  cardRight: { display: 'flex', alignItems: 'center', gap: '8px' },
  symbol: { fontFamily: 'monospace', fontSize: '15px', fontWeight: 'bold', color: '#ddd' },
  price: { fontFamily: 'monospace', fontSize: '12px', color: '#666' },
  badge: {
    fontFamily: 'monospace', fontSize: '11px', border: '1px solid',
    padding: '2px 8px', borderRadius: '3px',
  },
  structureLabel: { fontFamily: 'monospace', fontSize: '11px', color: '#888' },
  meta: {
    display: 'flex', gap: '12px', flexWrap: 'wrap',
    fontFamily: 'monospace', fontSize: '11px', color: '#666', marginBottom: '8px',
  },
  metrics: { display: 'flex', gap: '16px', flexWrap: 'wrap', marginBottom: '8px' },
  metric: { display: 'flex', flexDirection: 'column', gap: '2px' },
  metricLabel: { fontFamily: 'monospace', fontSize: '9px', color: '#555', letterSpacing: '0.08em' },
  metricVal: { fontFamily: 'monospace', fontSize: '13px', color: '#aaa', fontWeight: 'bold' },
  signals: { display: 'flex', gap: '8px', flexWrap: 'wrap', marginBottom: '8px' },
  signal: { fontFamily: 'monospace', fontSize: '10px' },
  copyBtn: {
    background: 'none', border: '1px solid #2a2a3e', color: '#555',
    cursor: 'pointer', padding: '3px 10px', borderRadius: '3px',
    fontFamily: 'monospace', fontSize: '10px',
  },
}
