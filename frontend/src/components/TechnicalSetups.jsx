import { useState, useEffect, useRef } from 'react'
import { fetchTechnicalSetups, triggerSetupsScan } from '../api.js'
import { saveNewTrade } from '../journal.js'
import SaveToJournalModal from './SaveToJournalModal.jsx'

// Each of the 7 signals is computed as a BULLISH test in the backend
// (score_signals in technical_scanner.py), and a bearish setup fires a badge
// when that test is FALSE. So the label and the tooltip must both flip with
// direction — otherwise a bearish card shows "✓ RS vs QQQ" hovering as
// "outperformed QQQ", which is the exact opposite of what the tick means.
//
// Note stage2 and rsi_zone are compound conditions, so their bearish form is a
// negation ("not in a Stage 2 uptrend"), not a positive bear signal. The text
// says so rather than overstating it.
const SIGNALS = {
  price_vs_ema21: {
    bullish: { label: 'Price>21EMA', tip: 'Price is above the 21-day EMA — short-term momentum is up' },
    bearish: { label: 'Price<21EMA', tip: 'Price is at or below the 21-day EMA — short-term momentum is down' },
  },
  ema_alignment: {
    bullish: { label: '13/21 EMA', tip: '13-day EMA is above the 21-day EMA — EMAs stacked bullishly' },
    bearish: { label: '13/21 EMA', tip: '13-day EMA is at or below the 21-day EMA — EMAs stacked bearishly' },
  },
  stage2: {
    bullish: { label: 'Stage 2', tip: 'Price > MA50 > MA200 — Minervini Stage 2 uptrend: price leads both averages' },
    bearish: { label: 'No Stage 2', tip: 'The Price > MA50 > MA200 stack does NOT hold. This is the absence of an uptrend, not proof of a downtrend' },
  },
  rsi_zone: {
    bullish: { label: 'RSI', tip: 'RSI(14) is between 45–75 and rising — in the momentum zone, not yet overbought' },
    bearish: { label: 'RSI', tip: 'RSI(14) is outside 45–75, or not rising — no bullish momentum. Does not by itself mean oversold' },
  },
  volume_accum: {
    bullish: { label: 'Volume', tip: '5-day average volume exceeds the 20-day average — accumulation' },
    bearish: { label: 'Volume', tip: '5-day average volume is at or below the 20-day average — no accumulation' },
  },
  rs_vs_qqq: {
    bullish: { label: 'RS vs QQQ', tip: 'Stock outperformed QQQ over the last 10 days — relative strength vs the index' },
    bearish: { label: 'RW vs QQQ', tip: 'Stock UNDERperformed QQQ over the last 10 days — relative weakness vs the index' },
  },
  breakout: {
    bullish: { label: 'Near High', tip: 'Price is within 5% of its 50-day high — coiling near a potential breakout' },
    bearish: { label: 'Off High', tip: 'Price is more than 5% below its 50-day high — not near a breakout level' },
  },
}

// Auto-generated thesis text for a saved journal entry — TechnicalSetup has
// no narrative field like TradeSetup's catalyst.catalyst_summary, so this
// reuses the same SIGNALS labels the card itself renders.
function signalsSummary(setup) {
  if (setup.setup_type === '200w_bounce') {
    return '200-week MA bounce — touched and reclaimed a rising 200-week moving average'
  }
  const dir = setup.direction === 'bearish' ? 'bearish' : 'bullish'
  const firing = Object.entries(SIGNALS)
    .filter(([key, variants]) => {
      const isBullishSignal = setup.signal_details[key]
      return dir === 'bullish' ? isBullishSignal : !isBullishSignal
    })
    .map(([, variants]) => variants[dir].label)
  return `${setup.signal_count}/7 ${dir} signals: ${firing.join(', ')}`
}

function SignalBadges({ details, direction }) {
  const dir = direction === 'bearish' ? 'bearish' : 'bullish'
  return (
    <div style={styles.signals}>
      {Object.entries(SIGNALS).map(([key, variants]) => {
        const isBullishSignal = details[key]
        // A bearish setup fires when the bullish test is false.
        const firing = dir === 'bullish' ? isBullishSignal : !isBullishSignal
        // Describe what the badge actually asserts in THIS direction.
        const { label, tip } = variants[dir]
        return (
          <span key={key} style={{ ...styles.signal, color: firing ? '#00ffaa' : '#333' }} title={tip}>
            {firing ? '✓' : '·'} {label}
          </span>
        )
      })}
    </div>
  )
}

const STRUCTURE_LABELS = {
  long_call:        'Long Call',
  long_put:         'Long Put',
  bull_call_spread: 'Bull Call Spread',
  bear_put_spread:  'Bear Put Spread',
}

function BounceFacts({ facts }) {
  if (!facts) return null
  return (
    <div style={styles.bounceFacts}>
      <span>200W MA ${facts.ma_200w?.toFixed(2)}</span>
      <span>MA slope {facts.ma_slope_pct >= 0 ? '+' : ''}{facts.ma_slope_pct}%</span>
      <span>Touched {facts.touch_pct <= 0 ? `${Math.abs(facts.touch_pct)}% below` : `${facts.touch_pct}% above`} the MA, {facts.weeks_since_touch}w ago</span>
      <span>Now {facts.extension_pct >= 0 ? '+' : ''}{facts.extension_pct}% off the MA</span>
    </div>
  )
}

function SetupCard({ setup, onSaveToJournal }) {
  const [copied, setCopied] = useState(false)
  const isBearish = setup.direction === 'bearish'
  const isBounce = setup.setup_type === '200w_bounce'
  const structureLabel = STRUCTURE_LABELS[setup.structure] ?? setup.structure

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
            {isBounce ? '200W MA BOUNCE' : `${setup.signal_count}/7 ${setup.direction.toUpperCase()}`}
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

      {isBounce
        ? <BounceFacts facts={setup.signal_details} />
        : <SignalBadges details={setup.signal_details} direction={setup.direction} />}

      <div style={{ display: 'flex', gap: '8px' }}>
        <button style={styles.copyBtn} onClick={copyOrder}>
          {copied ? '✓ Copied' : 'Copy Order'}
        </button>
        <button style={styles.copyBtn} onClick={() => onSaveToJournal(setup)}>
          Save to Journal
        </button>
      </div>
    </div>
  )
}

export default function TechnicalSetups() {
  const [setups, setSetups] = useState([])
  const [loading, setLoading] = useState(true)
  const [scanPhase, setScanPhase] = useState('idle') // 'idle' | 'scanning' | 'done'
  const [elapsed, setElapsed] = useState(0)
  const pollRef = useRef(null)
  const elapsedTimerRef = useRef(null)
  const startPollRef = useRef(null)
  const fallbackRef = useRef(null)
  const idleTimerRef = useRef(null)
  const [error, setError] = useState(null)
  const [scanTimestamp, setScanTimestamp] = useState(null)
  const [filters, setFilters] = useState({ direction: 'both', sort: 'rr' })
  const [saveTarget, setSaveTarget] = useState(null)
  const [contractCount, setContractCount] = useState(1)
  const [notes, setNotes] = useState('')

  function confirmSave() {
    if (!saveTarget) return
    const s = saveTarget
    const expiryStr = s.expiry
      ? new Date(s.expiry + 'T00:00:00').toLocaleDateString('en-US', { month: 'short', day: 'numeric' })
      : '—'
    const structureLabel = STRUCTURE_LABELS[s.structure] ?? s.structure
    saveNewTrade({
      symbol: s.symbol,
      structureLabel: `${structureLabel} ${expiryStr} $${s.strike}${s.short_strike ? `/$${s.short_strike}` : ''}`,
      entryDebit: s.premium,
      contracts: contractCount,
      thesis: signalsSummary(s),
      scoreAtEntry: s.signal_count,
      notes,
      longOcc: s.long_occ,
      shortOcc: s.short_occ,
    })
    setSaveTarget(null)
    setContractCount(1)
    setNotes('')
  }

  function clearAllTimers() {
    clearInterval(pollRef.current)
    clearInterval(elapsedTimerRef.current)
    clearTimeout(startPollRef.current)
    clearTimeout(fallbackRef.current)
    clearTimeout(idleTimerRef.current)
  }

  useEffect(() => () => clearAllTimers(), [])

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
    setScanPhase('scanning')
    setElapsed(0)

    let baseline = scanTimestamp ?? null
    try {
      const [, result] = await Promise.all([triggerSetupsScan(), fetchTechnicalSetups(filters)])
      baseline = result.scan_timestamp ?? baseline
    } catch (e) {
      setError(e.message)
      setScanPhase('idle')
      return
    }

    elapsedTimerRef.current = setInterval(() => setElapsed(e => e + 1), 1000)

    // Wait 15s before polling — scan takes ~45s minimum
    startPollRef.current = setTimeout(() => {
      pollRef.current = setInterval(async () => {
        try {
          const result = await fetchTechnicalSetups(filters)
          if (result.scan_timestamp && result.scan_timestamp !== baseline) {
            clearAllTimers()
            setScanPhase('done')
            await load()
            idleTimerRef.current = setTimeout(() => setScanPhase('idle'), 2000)
          }
        } catch (_) {}
      }, 8000)
    }, 15000)

    // Absolute fallback at 90s
    fallbackRef.current = setTimeout(async () => {
      clearAllTimers()
      await load()
      setScanPhase('done')
      idleTimerRef.current = setTimeout(() => setScanPhase('idle'), 2000)
    }, 90000)
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
            <span style={styles.scanTime}>Last scan: {scanTime} ET</span>
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
              : '▶ SCAN SETUPS'}
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
        <div style={styles.errorBanner}>Could not fetch setups: {error}</div>
      )}

      {/* Filters */}
      <div style={styles.filterBar}>
        <div style={styles.filterGroup}>
          <span style={styles.filterLabel} title="Filter by trade direction — bullish setups use calls/bull spreads, bearish use puts/bear spreads">DIRECTION</span>
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
          <span style={styles.filterLabel} title="Sort order for the results list">SORT</span>
          <button
            title="Sort by risk-to-reward ratio — highest reward per dollar risked first"
            style={{ ...styles.filterBtn, ...(filters.sort === 'rr' ? styles.filterBtnActive : {}) }}
            onClick={() => setFilters(f => ({ ...f, sort: 'rr' }))}
          >R:R</button>
          <button
            title="Sort by probability of profit — highest chance of expiring in-the-money first"
            style={{ ...styles.filterBtn, ...(filters.sort === 'pop' ? styles.filterBtnActive : {}) }}
            onClick={() => setFilters(f => ({ ...f, sort: 'pop' }))}
          >PoP</button>
        </div>
      </div>

      {/* Results */}
      {!loading && setups.length === 0 && scanPhase === 'idle' && (
        <div style={styles.empty}>
          {scanTimestamp
            ? 'No setups clear the bar right now. Try running a fresh scan.'
            : 'No scan data yet. Click ▶ SCAN SETUPS to run the first scan.'}
        </div>
      )}

      <div style={{ paddingBottom: '32px' }}>
        {setups.map((setup, i) => (
          <SetupCard
            key={`${setup.symbol}-${setup.structure}-${i}`}
            setup={setup}
            onSaveToJournal={setSaveTarget}
          />
        ))}
      </div>

      {saveTarget && (
        <SaveToJournalModal
          symbolLine={`${saveTarget.symbol} — ${STRUCTURE_LABELS[saveTarget.structure] ?? saveTarget.structure} $${saveTarget.strike}${saveTarget.short_strike ? `/$${saveTarget.short_strike}` : ''}`}
          unitCost={saveTarget.premium}
          contracts={contractCount}
          onContractsChange={setContractCount}
          notes={notes}
          onNotesChange={setNotes}
          onCancel={() => setSaveTarget(null)}
          onConfirm={confirmSave}
        />
      )}
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
  bounceFacts: { display: 'flex', flexWrap: 'wrap', gap: '12px', padding: '0 16px 8px', fontSize: '11px', color: '#888', fontFamily: 'monospace' },
  signal: { fontFamily: 'monospace', fontSize: '10px' },
  copyBtn: {
    background: 'none', border: '1px solid #2a2a3e', color: '#555',
    cursor: 'pointer', padding: '3px 10px', borderRadius: '3px',
    fontFamily: 'monospace', fontSize: '10px',
  },
}
