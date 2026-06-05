import { useState, useEffect, useRef } from 'react'
import { fetchCeltSetups, triggerCeltScan, fetchHealth } from '../api.js'

function ScoreBar({ label, value, max }) {
  const pct = Math.min(100, (value / max) * 100)
  return (
    <div style={styles.scoreBar}>
      <span style={styles.scoreBarLabel}>{label}</span>
      <div style={styles.scoreBarTrack}>
        <div style={{ ...styles.scoreBarFill, width: `${pct}%` }} />
      </div>
      <span style={styles.scoreBarVal}>{value.toFixed(2)}</span>
    </div>
  )
}

function CeltCard({ setup }) {
  const [expanded, setExpanded] = useState(false)

  const totalColor = setup.signal_score >= 2.8 ? '#00ffaa' : '#ffaa00'

  const expiryStr = setup.leap_expiry
    ? new Date(setup.leap_expiry + 'T00:00:00').toLocaleDateString('en-US', { month: 'short', day: 'numeric', year: '2-digit' })
    : '—'

  const confidenceColor = setup.confidence >= 75 ? '#00ffaa' : setup.confidence >= 60 ? '#ffaa00' : '#888'

  return (
    <div style={styles.card}>
      <div style={styles.cardHeader} onClick={() => setExpanded(e => !e)}>
        <div style={styles.cardLeft}>
          <span style={styles.symbol}>{setup.symbol}</span>
          <span style={styles.price}>${setup.stock_price?.toFixed(2)}</span>
          <span style={{ ...styles.drawdownBadge, color: setup.drawdown_pct >= 30 ? '#ff4444' : '#ffaa00' }}>
            ↓{setup.drawdown_pct?.toFixed(0)}%
          </span>
        </div>
        <div style={styles.cardRight}>
          <span style={{ ...styles.totalScore, color: totalColor }}>
            {setup.signal_score?.toFixed(2)}
          </span>
          <span style={{ ...styles.confidenceBadge, color: confidenceColor, borderColor: confidenceColor }}>
            {setup.confidence}%
          </span>
          <span style={styles.expandToggle}>{expanded ? '▲' : '▼'}</span>
        </div>
      </div>

      {/* Signal score mini bars */}
      <div style={styles.scoreBars}>
        <ScoreBar label="Price" value={setup.price_damage_score} max={1.0} />
        <ScoreBar label="HV" value={setup.volatility_score} max={1.0} />
        <ScoreBar label="Sent" value={setup.sentiment_score} max={1.2} />
      </div>

      {/* LEAP summary row */}
      <div style={styles.leapRow}>
        <span style={styles.leapLabel}>LEAP</span>
        <span style={styles.leapStrike}>${setup.leap_strike?.toFixed(0)}</span>
        <span style={styles.leapExpiry}>{expiryStr}</span>
        <span style={{ ...styles.leapMeta, color: '#aaa' }}>Δ{setup.leap_delta?.toFixed(2)}</span>
        <span style={styles.leapMeta}>{setup.leap_dte}d</span>
        <span style={{ ...styles.leapMeta, color: '#00ffaa' }}>${setup.leap_ask?.toFixed(2)} ask</span>
        <span style={{ ...styles.leapMeta, color: '#555' }}>OI {setup.leap_oi?.toLocaleString()}</span>
      </div>

      {/* Expanded detail */}
      {expanded && (
        <div style={styles.detail}>
          <div style={styles.detailGrid}>
            <div style={styles.detailItem}>
              <span style={styles.detailLabel}>IV RANK</span>
              <span style={{ ...styles.detailVal, color: setup.iv_rank >= 70 ? '#ffaa00' : '#aaa' }}>
                {setup.iv_rank?.toFixed(0)}
              </span>
            </div>
            <div style={styles.detailItem}>
              <span style={styles.detailLabel}>HV30</span>
              <span style={styles.detailVal}>{(setup.hv30 * 100)?.toFixed(1)}%</span>
            </div>
            <div style={styles.detailItem}>
              <span style={styles.detailLabel}>HV60</span>
              <span style={styles.detailVal}>{(setup.hv60 * 100)?.toFixed(1)}%</span>
            </div>
            <div style={styles.detailItem}>
              <span style={styles.detailLabel}>HV RATIO</span>
              <span style={{ ...styles.detailVal, color: setup.hv_ratio >= 2.0 ? '#00ffaa' : '#aaa' }}>
                {setup.hv_ratio?.toFixed(2)}×
              </span>
            </div>
            <div style={styles.detailItem}>
              <span style={styles.detailLabel}>HV EXPANSION</span>
              <span style={styles.detailVal}>{setup.hv_expansion?.toFixed(2)}×</span>
            </div>
            <div style={styles.detailItem}>
              <span style={styles.detailLabel}>P/C OI RATIO</span>
              <span style={styles.detailVal}>{setup.leap_put_call_oi_ratio?.toFixed(2)}</span>
            </div>
            <div style={styles.detailItem}>
              <span style={styles.detailLabel}>200 SMA</span>
              <span style={{ ...styles.detailVal, color: setup.below_200sma ? '#ff4444' : '#666' }}>
                {setup.pct_from_200sma > 0 ? '+' : ''}{setup.pct_from_200sma?.toFixed(1)}%
              </span>
            </div>
            <div style={styles.detailItem}>
              <span style={styles.detailLabel}>LEAP IV</span>
              <span style={styles.detailVal}>{(setup.leap_iv * 100)?.toFixed(1)}%</span>
            </div>
            <div style={styles.detailItem}>
              <span style={styles.detailLabel}>LEAP BID</span>
              <span style={styles.detailVal}>${setup.leap_bid?.toFixed(2)}</span>
            </div>
            <div style={styles.detailItem}>
              <span style={styles.detailLabel}>LEAP MID</span>
              <span style={styles.detailVal}>${setup.leap_mid?.toFixed(2)}</span>
            </div>
          </div>
          {setup.entry_notes && (
            <div style={styles.notes}>{setup.entry_notes}</div>
          )}
        </div>
      )}
    </div>
  )
}

export default function CeltSetups() {
  const [setups, setSetups] = useState([])
  const [loading, setLoading] = useState(true)
  const [scanPhase, setScanPhase] = useState('idle')
  const [elapsed, setElapsed] = useState(0)
  const [error, setError] = useState(null)
  const [scanTimestamp, setScanTimestamp] = useState(null)
  const [filters, setFilters] = useState({ minScore: 2.2, sort: 'score' })

  const pollRef = useRef(null)
  const elapsedTimerRef = useRef(null)
  const startPollRef = useRef(null)
  const fallbackRef = useRef(null)
  const idleTimerRef = useRef(null)

  function clearAllTimers() {
    clearInterval(pollRef.current)
    clearInterval(elapsedTimerRef.current)
    clearTimeout(startPollRef.current)
    clearTimeout(fallbackRef.current)
    clearTimeout(idleTimerRef.current)
  }

  useEffect(() => () => clearAllTimers(), [])
  useEffect(() => { load() }, [filters])

  async function load() {
    try {
      const data = await fetchCeltSetups(filters)
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
    setScanPhase('scanning')
    setElapsed(0)

    let baseline = null
    try {
      const health = await fetchHealth()
      baseline = health.celt_last_scan ?? null
      await triggerCeltScan()
    } catch (e) {
      setError(e.message)
      setScanPhase('idle')
      return
    }

    elapsedTimerRef.current = setInterval(() => setElapsed(e => e + 1), 1000)

    // Wait 20s before polling — CELT scan takes ~60s
    startPollRef.current = setTimeout(() => {
      pollRef.current = setInterval(async () => {
        try {
          const health = await fetchHealth()
          if (health.celt_last_scan && health.celt_last_scan !== baseline) {
            clearAllTimers()
            setScanPhase('done')
            await load()
            idleTimerRef.current = setTimeout(() => setScanPhase('idle'), 2000)
          }
        } catch (_) {}
      }, 10000)
    }, 20000)

    // Absolute fallback at 120s
    fallbackRef.current = setTimeout(async () => {
      clearAllTimers()
      await load()
      setScanPhase('done')
      idleTimerRef.current = setTimeout(() => setScanPhase('idle'), 2000)
    }, 120000)
  }

  const scanTime = scanTimestamp
    ? new Date(scanTimestamp).toLocaleTimeString('en-US', { hour: '2-digit', minute: '2-digit', timeZone: 'America/Los_Angeles' })
    : '—'

  return (
    <div>
      <div style={styles.header}>
        <div>
          <span style={styles.title}>CRASH ENTRY LEAPS</span>
          <span style={styles.count}>{loading ? '…' : `${setups.length} setups`}</span>
        </div>
        <div style={styles.headerRight}>
          {scanTimestamp && (
            <span style={styles.scanTime}>Last scan: {scanTime}</span>
          )}
          <button
            style={{
              ...styles.scanBtn,
              ...(scanPhase === 'scanning' ? styles.scanBtnActive : {}),
              ...(scanPhase === 'done' ? { background: '#00ffaa', color: '#000', opacity: 1 } : {}),
            }}
            onClick={runScan}
            disabled={scanPhase !== 'idle'}
          >
            {scanPhase === 'scanning' ? `⟳ SCANNING... ${elapsed}s`
              : scanPhase === 'done' ? '✓ DONE'
              : '▶ RUN SCAN'}
          </button>
        </div>
      </div>

      {scanPhase === 'scanning' && (
        <div style={{ position: 'relative', height: '2px', overflow: 'hidden' }}>
          <div style={{
            position: 'absolute', top: 0, left: 0,
            width: '25%', height: '100%',
            background: 'linear-gradient(90deg, transparent, rgba(0,255,170,0.6), transparent)',
            animation: 'shimmer-sweep 1.5s ease-in-out infinite',
          }} />
        </div>
      )}

      {error && (
        <div style={styles.errorBanner}>Could not fetch CELT setups: {error}</div>
      )}

      <div style={styles.filterBar}>
        <div style={styles.filterGroup}>
          <span style={styles.filterLabel} title="Minimum total signal score (max 3.2). 2.2 = baseline qualify, 2.8+ = high conviction.">MIN SCORE</span>
          <input
            type="range" min="2.0" max="3.2" step="0.1"
            value={filters.minScore}
            onChange={e => setFilters(f => ({ ...f, minScore: parseFloat(e.target.value) }))}
            style={styles.slider}
          />
          <span style={styles.filterVal}>{filters.minScore.toFixed(1)}</span>
        </div>
        <div style={styles.filterGroup}>
          <span style={styles.filterLabel} title="Sort order">SORT</span>
          {[['score', 'Score'], ['drawdown', 'Drawdown'], ['ivrank', 'IV Rank']].map(([val, label]) => (
            <button
              key={val}
              style={{ ...styles.filterBtn, ...(filters.sort === val ? styles.filterBtnActive : {}) }}
              onClick={() => setFilters(f => ({ ...f, sort: val }))}
            >
              {label}
            </button>
          ))}
        </div>
      </div>

      <div style={styles.legend}>
        <span>Score bars: <span style={{ color: '#00ffaa' }}>■</span> Price Damage</span>
        <span><span style={{ color: '#00ffaa' }}>■</span> HV Elevation</span>
        <span><span style={{ color: '#00ffaa' }}>■</span> Sentiment</span>
        <span style={{ color: '#555' }}>Click a card to expand details</span>
      </div>

      {!loading && setups.length === 0 && scanPhase === 'idle' && (
        <div style={styles.empty}>
          {scanTimestamp
            ? 'No CELT setups meet your filters. Try lowering the min score or running a fresh scan.'
            : 'No scan data yet. Click ▶ RUN SCAN to run the first CELT scan (~60s).'}
        </div>
      )}

      <div style={{ paddingBottom: '32px' }}>
        {setups.map((setup, i) => (
          <CeltCard key={`${setup.symbol}-${i}`} setup={setup} />
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
  filterVal: { fontFamily: 'monospace', fontSize: '11px', color: '#aaa', minWidth: '28px' },
  legend: {
    display: 'flex', gap: '16px', padding: '6px 16px',
    fontFamily: 'monospace', fontSize: '10px', color: '#444',
    borderBottom: '1px solid #111',
  },
  empty: {
    padding: '48px 16px', textAlign: 'center',
    color: '#555', fontFamily: 'monospace', fontSize: '13px',
  },
  card: {
    background: '#0d0d1a', border: '1px solid #1a1a2e',
    borderLeft: '3px solid #00ffaa',
    borderRadius: '4px', padding: '12px 16px', margin: '8px 16px',
    cursor: 'pointer',
  },
  cardHeader: {
    display: 'flex', justifyContent: 'space-between',
    alignItems: 'center', marginBottom: '8px', flexWrap: 'wrap', gap: '8px',
  },
  cardLeft: { display: 'flex', alignItems: 'baseline', gap: '10px' },
  cardRight: { display: 'flex', alignItems: 'center', gap: '8px' },
  symbol: { fontFamily: 'monospace', fontSize: '15px', fontWeight: 'bold', color: '#ddd' },
  price: { fontFamily: 'monospace', fontSize: '12px', color: '#666' },
  drawdownBadge: { fontFamily: 'monospace', fontSize: '12px', fontWeight: 'bold' },
  totalScore: {
    fontFamily: 'monospace', fontSize: '18px', fontWeight: 'bold',
  },
  confidenceBadge: {
    fontFamily: 'monospace', fontSize: '11px', border: '1px solid',
    padding: '2px 8px', borderRadius: '3px',
  },
  expandToggle: { fontFamily: 'monospace', fontSize: '10px', color: '#444' },
  scoreBars: { display: 'flex', gap: '16px', marginBottom: '8px', flexWrap: 'wrap' },
  scoreBar: { display: 'flex', alignItems: 'center', gap: '6px' },
  scoreBarLabel: { fontFamily: 'monospace', fontSize: '9px', color: '#555', width: '28px', letterSpacing: '0.05em' },
  scoreBarTrack: {
    width: '60px', height: '4px', background: '#1a1a2e', borderRadius: '2px', overflow: 'hidden',
  },
  scoreBarFill: { height: '100%', background: '#00ffaa', borderRadius: '2px' },
  scoreBarVal: { fontFamily: 'monospace', fontSize: '10px', color: '#888', minWidth: '28px' },
  leapRow: {
    display: 'flex', gap: '12px', alignItems: 'center',
    padding: '6px 0', borderTop: '1px solid #111', flexWrap: 'wrap',
  },
  leapLabel: { fontFamily: 'monospace', fontSize: '9px', color: '#555', letterSpacing: '0.1em' },
  leapStrike: { fontFamily: 'monospace', fontSize: '13px', color: '#ddd', fontWeight: 'bold' },
  leapExpiry: { fontFamily: 'monospace', fontSize: '11px', color: '#888' },
  leapMeta: { fontFamily: 'monospace', fontSize: '11px', color: '#666' },
  detail: { borderTop: '1px solid #111', marginTop: '8px', paddingTop: '10px' },
  detailGrid: { display: 'flex', gap: '16px', flexWrap: 'wrap', marginBottom: '8px' },
  detailItem: { display: 'flex', flexDirection: 'column', gap: '2px' },
  detailLabel: { fontFamily: 'monospace', fontSize: '9px', color: '#555', letterSpacing: '0.08em' },
  detailVal: { fontFamily: 'monospace', fontSize: '12px', color: '#aaa', fontWeight: 'bold' },
  notes: {
    fontFamily: 'monospace', fontSize: '11px', color: '#666',
    fontStyle: 'italic', paddingTop: '4px',
  },
}
