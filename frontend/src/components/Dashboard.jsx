import { useState, useEffect, useRef } from 'react'
import MarketContext from './MarketContext.jsx'
import FilterBar from './FilterBar.jsx'
import OpportunityCard from './OpportunityCard.jsx'
import OpportunityTable from './OpportunityTable.jsx'
import SaveToJournalModal from './SaveToJournalModal.jsx'
import { triggerScan, fetchHealth } from '../api.js'
import { saveNewTrade } from '../journal.js'

const STRUCTURE_LABELS = {
  bull_call_spread: 'Bull Call Spread',
  bear_put_spread: 'Bear Put Spread',
  calendar: 'Calendar Spread',
  long_call: 'Long Call',
}

const DATA_STALE_THRESHOLD = 90 * 60  // 90 minutes in seconds

function isMarketOpen() {
  const now = new Date()
  const et = new Date(now.toLocaleString('en-US', { timeZone: 'America/New_York' }))
  const day = et.getDay()
  if (day === 0 || day === 6) return false
  const h = et.getHours()
  const m = et.getMinutes()
  const minutes = h * 60 + m
  return minutes >= 9 * 60 + 30 && minutes <= 16 * 60
}

function sortOpportunities(opps, sort) {
  const list = [...opps]
  switch (sort) {
    case 'rr': return list.sort((a, b) => b.rr_ratio - a.rr_ratio)
    case 'debit': return list.sort((a, b) => a.net_debit - b.net_debit)
    case 'symbol': return list.sort((a, b) => a.symbol.localeCompare(b.symbol))
    default: return list.sort((a, b) => b.score - a.score)
  }
}

function SkeletonCard() {
  return (
    <div style={{
      background: '#0d0d1a',
      border: '1px solid #1a1a2e',
      borderRadius: '6px',
      margin: '8px 16px',
      padding: '16px',
      animation: 'pulse 1.5s ease-in-out infinite',
    }}>
      <div style={{ height: '14px', background: '#1a1a2e', borderRadius: '3px', width: '40%', marginBottom: '8px' }} />
      <div style={{ height: '11px', background: '#111', borderRadius: '3px', width: '70%' }} />
    </div>
  )
}

export default function Dashboard({ data, loading, error, filters, onFiltersChange, onRefresh }) {
  const [saveTarget, setSaveTarget] = useState(null)
  const [view, setView] = useState('table')
  const [contractCount, setContractCount] = useState(1)
  const [notes, setNotes] = useState('')
  const [scanPhase, setScanPhase] = useState('idle') // 'idle' | 'scanning' | 'done'
  // `error` is a prop (fetch failures from App); scan-trigger and scan-failure
  // messages are local, so they can be raised and cleared independently.
  const [scanError, setScanError] = useState(null)
  const [elapsed, setElapsed] = useState(0)
  const pollRef = useRef(null)
  const elapsedTimerRef = useRef(null)
  const fallbackRef = useRef(null)
  const idleTimerRef = useRef(null)

  function clearAllTimers() {
    clearInterval(pollRef.current)
    clearInterval(elapsedTimerRef.current)
    clearTimeout(fallbackRef.current)
    clearTimeout(idleTimerRef.current)
  }

  useEffect(() => () => clearAllTimers(), [])

  async function runScan() {
    setScanPhase('scanning')
    setScanError(null)
    setElapsed(0)

    // Read the baseline BEFORE triggering, not concurrently — running these in
    // parallel races the trigger against the read, and a health response that
    // landed after the scan finished would leave the poller waiting forever.
    let baseline = data?.scan_timestamp ?? null
    try {
      const health = await fetchHealth()
      baseline = health.last_scan ?? baseline
      await triggerScan()
    } catch (e) {
      setScanPhase('idle')
      setScanError(e.message || 'Could not start scan')
      return
    }

    elapsedTimerRef.current = setInterval(() => setElapsed(e => e + 1), 1000)

    pollRef.current = setInterval(async () => {
      try {
        const health = await fetchHealth()
        if (health.last_scan_error) {
          clearAllTimers()
          setScanPhase('idle')
          setScanError(`Scan failed: ${health.last_scan_error.error}`)
          return
        }
        if (health.last_scan && health.last_scan !== baseline) {
          clearAllTimers()
          setScanPhase('done')
          await onRefresh()
          idleTimerRef.current = setTimeout(() => setScanPhase('idle'), 2000)
        }
      } catch (_) {}
    }, 6000)

    // A full scan is 93 symbols paced to stay under Schwab's rate limit, so it
    // runs ~2-3 minutes, longer from a cold start. The old 60s fallback fired
    // mid-scan and reported "✓ DONE" over unchanged, stale prices. Time out
    // rather than claim success.
    fallbackRef.current = setTimeout(() => {
      clearAllTimers()
      setScanPhase('idle')
      setScanError('Scan did not report completion within 5 minutes — it may still be running. Refresh shortly.')
    }, 300000)
  }

  const marketOpen = isMarketOpen()
  const ageSeconds = data?.data_age_seconds ?? -1
  const isStale = ageSeconds > DATA_STALE_THRESHOLD

  const opps = data?.opportunities ?? []
  const sorted = sortOpportunities(opps, filters.sort)

  const scanTime = data?.scan_timestamp
    ? new Date(data.scan_timestamp).toLocaleTimeString('en-US', {
        hour: '2-digit', minute: '2-digit', timeZone: 'America/New_York'
      })
    : '—'

  function saveToJournal(setup) {
    setSaveTarget(setup)
    setContractCount(1)
    setNotes('')
  }

  function confirmSave() {
    if (!saveTarget) return
    const expiryStr = new Date(saveTarget.expiry + 'T00:00:00')
      .toLocaleDateString('en-US', { month: 'short', day: 'numeric' })
    saveNewTrade({
      symbol: saveTarget.symbol,
      structureLabel: `${STRUCTURE_LABELS[saveTarget.structure] ?? saveTarget.structure} ${expiryStr} $${saveTarget.long_strike}/$${saveTarget.short_strike}`,
      entryDebit: saveTarget.net_debit,
      contracts: contractCount,
      thesis: saveTarget.catalyst.catalyst_summary,
      scoreAtEntry: saveTarget.score,
      notes,
      longOcc: saveTarget.long_occ,
      shortOcc: saveTarget.short_occ,
    })
    setSaveTarget(null)
  }

  return (
    <div>
      {/* Header */}
      <div style={styles.header}>
        <div>
          <span style={styles.title}>QQQ OPTIONS SCANNER</span>
          <span style={styles.count}>
            {loading ? '…' : `${sorted.length} opportunities`}
          </span>
        </div>
        <div style={styles.headerRight}>
          <span style={{ color: marketOpen ? '#00ffaa' : '#555', fontSize: '12px', fontFamily: 'monospace' }}>
            {marketOpen ? '● MARKET OPEN' : '○ MARKET CLOSED'}
          </span>
          <span style={{ color: '#555', fontSize: '12px', fontFamily: 'monospace' }}>
            Last scan: {scanTime} ET
          </span>
          <div style={styles.viewToggle}>
            <button
              style={{ ...styles.viewBtn, ...(view === 'table' ? styles.viewBtnActive : {}) }}
              onClick={() => setView('table')}
              title="Table view"
            >⊞ TABLE</button>
            <button
              style={{ ...styles.viewBtn, ...(view === 'cards' ? styles.viewBtnActive : {}) }}
              onClick={() => setView('cards')}
              title="Card view"
            >≡ CARDS</button>
          </div>
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
          <button style={styles.refreshBtn} onClick={onRefresh}>↻</button>
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

      {/* Staleness banner */}
      {isStale && (
        <div style={styles.staleBanner}>
          ⚠ Data is over 90 minutes old. Backend may be sleeping.
          Last scan: {scanTime} ET.
        </div>
      )}

      {/* Scan error banner — trigger failures and backend-reported scan failures */}
      {scanError && (
        <div style={styles.errorBanner}>{scanError}</div>
      )}

      {/* Error banner */}
      {error && (
        <div style={styles.errorBanner}>
          Could not fetch latest data: {error}
          {data && ` — Showing scan from ${scanTime} ET`}
        </div>
      )}

      {/* Market context */}
      <MarketContext marketContext={data?.market_context} />

      {/* Filter bar */}
      <FilterBar filters={filters} onChange={onFiltersChange} />

      {/* Market closed notice */}
      {!marketOpen && !loading && (
        <div style={styles.closedBanner}>
          Market is closed. Showing results from last morning scan.
        </div>
      )}

      {/* Opportunities */}
      {loading && !data ? (
        <div style={{ paddingBottom: '32px' }}>
          <SkeletonCard /><SkeletonCard /><SkeletonCard />
        </div>
      ) : view === 'table' ? (
        <OpportunityTable opportunities={sorted} onSaveToJournal={saveToJournal} />
      ) : (
        <div style={{ paddingBottom: '32px' }}>
          {sorted.length === 0 && (
            <div style={styles.empty}>
              No opportunities clear the bar right now. Check back after the next scan.
            </div>
          )}
          {sorted.map((setup, i) => (
            <OpportunityCard
              key={`${setup.symbol}-${setup.signal.detector}-${i}`}
              setup={setup}
              onSaveToJournal={saveToJournal}
            />
          ))}
        </div>
      )}

      {/* Save to journal modal */}
      {saveTarget && (
        <SaveToJournalModal
          symbolLine={`${saveTarget.symbol} — ${saveTarget.structure} $${saveTarget.long_strike}/$${saveTarget.short_strike}`}
          unitCost={saveTarget.net_debit}
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
    display: 'flex',
    justifyContent: 'space-between',
    alignItems: 'center',
    padding: '16px',
    borderBottom: '1px solid #1a1a2e',
    background: '#0a0a14',
    flexWrap: 'wrap',
    gap: '8px',
  },
  title: {
    fontFamily: 'monospace',
    fontSize: '16px',
    fontWeight: 'bold',
    color: '#00ffaa',
    marginRight: '16px',
    letterSpacing: '0.05em',
  },
  count: {
    fontFamily: 'monospace',
    fontSize: '13px',
    color: '#666',
  },
  headerRight: {
    display: 'flex',
    alignItems: 'center',
    gap: '16px',
  },
  scanBtn: {
    background: 'none',
    border: '1px solid #00ffaa',
    color: '#00ffaa',
    cursor: 'pointer',
    padding: '4px 12px',
    borderRadius: '3px',
    fontFamily: 'monospace',
    fontSize: '11px',
    letterSpacing: '0.05em',
  },
  scanBtnActive: {
    color: '#555',
    borderColor: '#333',
    cursor: 'not-allowed',
  },
  refreshBtn: {
    background: 'none',
    border: '1px solid #2a2a3e',
    color: '#666',
    cursor: 'pointer',
    padding: '4px 8px',
    borderRadius: '3px',
    fontSize: '16px',
  },
  viewToggle: {
    display: 'flex',
    gap: '2px',
  },
  viewBtn: {
    background: 'none',
    border: '1px solid #2a2a3e',
    color: '#555',
    cursor: 'pointer',
    padding: '3px 10px',
    fontFamily: 'monospace',
    fontSize: '11px',
    letterSpacing: '0.05em',
    borderRadius: '3px',
  },
  viewBtnActive: {
    border: '1px solid #00ffaa',
    color: '#00ffaa',
    background: '#0a1a0f',
  },
  staleBanner: {
    background: '#1a1000',
    borderLeft: '4px solid #ffaa00',
    color: '#ffaa00',
    padding: '8px 16px',
    fontSize: '12px',
    fontFamily: 'monospace',
  },
  errorBanner: {
    background: '#1a0505',
    borderLeft: '4px solid #ff4444',
    color: '#ff4444',
    padding: '8px 16px',
    fontSize: '12px',
    fontFamily: 'monospace',
  },
  closedBanner: {
    background: '#0f0f0f',
    color: '#555',
    padding: '8px 16px',
    fontSize: '12px',
    fontFamily: 'monospace',
    textAlign: 'center',
    borderBottom: '1px solid #1a1a2e',
  },
  empty: {
    padding: '48px 16px',
    textAlign: 'center',
    color: '#555',
    fontFamily: 'monospace',
    fontSize: '13px',
  },
}
