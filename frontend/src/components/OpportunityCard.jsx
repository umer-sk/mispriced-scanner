import { useState } from 'react'
import IVRankMeter from './IVRankMeter.jsx'
import SwingPnL from './SwingPnL.jsx'

const DETECTOR_LABELS = {
  iv_rank:        'IV Rank',
  skew:           'Skew Anomaly',
  parity:         'Parity Violation',
  term:           'Term Structure',
  move:           'Move Underpricing',
  put_iv_rank:    'Put IV Rank',
  skew_inversion: 'Skew Inversion',
  put_parity:     'Put Parity',
  downside_move:  'Downside Move',
}

function scoreColor(score) {
  if (score >= 75) return '#00ffaa'
  if (score >= 55) return '#ffaa00'
  return '#ff4444'
}

const ACCOUNT_SIZE = 40000

export default function OpportunityCard({ setup, onSaveToJournal, defaultExpanded = false }) {
  const [expanded, setExpanded] = useState(defaultExpanded)
  const [copied, setCopied] = useState(false)
  const [scoreExpanded, setScoreExpanded] = useState(false)
  const [riskPct, setRiskPct] = useState(() => parseFloat(localStorage.getItem('qqq_risk_pct') || '2.0'))

  const {
    symbol, stock_price, signal, catalyst,
    structure, long_strike, short_strike, expiry, dte,
    net_debit, max_gain, max_loss, breakeven, breakeven_move_pct, rr_ratio,
    probability_of_profit, net_delta, net_theta, net_vega,
    long_leg_oi, short_leg_oi, long_leg_volume, long_leg_spread_pct, short_leg_spread_pct,
    score, order_string,
  } = setup

  const isBearish = structure === 'bear_put_spread'

  const structureLabel = {
    bull_call_spread: 'Bull Call Spread',
    bear_put_spread:  'Bear Put Spread',
    calendar:         'Calendar Spread',
    long_call:        'Long Call',
  }[structure] ?? structure

  const scoreLabel = (() => {
    const detectorName = signal.detector ? (DETECTOR_LABELS[signal.detector] || signal.detector) : '—'
    const trend = setup.technical_context?.trend
    const trendStr = trend ? ` | ${trend.charAt(0).toUpperCase() + trend.slice(1)}` : ''
    return `${detectorName}${trendStr}`
  })()

  const suggestedContracts = net_debit > 0
    ? Math.max(1, Math.floor((ACCOUNT_SIZE * riskPct / 100) / (net_debit * 100)))
    : 0
  const maxLossDollars = suggestedContracts * net_debit * 100

  const ivRank = signal.raw_data?.iv_rank ?? 50

  const expiryStr = expiry
    ? new Date(expiry + 'T00:00:00').toLocaleDateString('en-US', { month: 'short', day: 'numeric' })
    : '—'

  function copyOrder() {
    navigator.clipboard.writeText(order_string).then(() => {
      setCopied(true)
      setTimeout(() => setCopied(false), 2000)
    })
  }

  return (
    <div style={{ ...styles.card, borderLeft: `3px solid ${isBearish ? '#ff4444' : '#00ffaa'}` }}>
      {/* Collapsed header — always visible */}
      <div style={styles.header} onClick={() => setExpanded(e => !e)}>
        <div style={styles.headerLeft}>
          <span style={styles.symbol}>{symbol}</span>
          <span style={styles.price}>${stock_price?.toFixed(2)}</span>
          <span
            style={{ ...styles.score, color: scoreColor(score), cursor: 'pointer' }}
            onClick={e => { e.stopPropagation(); setScoreExpanded(v => !v) }}
          >
            Score: {score}/100 {scoreExpanded ? '▲' : '▼'}
          </span>
          <span style={styles.scoreLabel}>{scoreLabel}</span>
          <span style={styles.ivr}>IVR: {ivRank?.toFixed(0)}%</span>
        </div>
        <div style={styles.headerRight}>
          <span style={styles.detector}>{DETECTOR_LABELS[signal.detector] || signal.detector}</span>
          <span style={styles.expand}>{expanded ? '▲' : '▼'}</span>
        </div>
      </div>

      {scoreExpanded && setup.score_breakdown && (
        <div style={{ padding: '0 16px 8px' }}>
          <div style={styles.breakdown}>
            {setup.score_breakdown.map((item, i) => (
              <div key={i} style={styles.breakdownRow}>
                <span style={styles.breakdownLabel}>{item.label}</span>
                <span style={styles.breakdownPts}>+{item.pts}</span>
              </div>
            ))}
            <div style={{ ...styles.breakdownRow, borderTop: '1px solid #2a2a3e', marginTop: '4px', paddingTop: '4px' }}>
              <span style={styles.breakdownLabel}>Total</span>
              <span style={{ ...styles.breakdownPts, color: scoreColor(score) }}>{score}</span>
            </div>
          </div>
        </div>
      )}

      <div style={styles.subHeader} onClick={() => setExpanded(e => !e)}>
        <span style={styles.structure}>
          {structureLabel} · {expiryStr} ${long_strike?.toFixed(0)}/{short_strike?.toFixed(0)} · DTE {dte}
        </span>
        <span style={styles.metrics}>
          Debit ${net_debit?.toFixed(2)} · R:R {rr_ratio?.toFixed(2)}:1 · BE +{breakeven_move_pct?.toFixed(1)}%
        </span>
      </div>

      {/* Expanded content */}
      {expanded && (
        <div style={styles.body}>
          <hr style={styles.divider} />

          {/* Technical context block */}
          {setup.technical_context && (
            <div style={styles.techBlock}>
              <div style={styles.techHeader}>
                <span style={styles.techSymbol}>{symbol}</span>
                <span style={styles.techPrice}>${setup.technical_context.price?.toFixed(2)}</span>
                <span style={{
                  ...styles.techTrend,
                  color: setup.technical_context.trend === 'uptrend' ? '#00ffaa'
                       : setup.technical_context.trend === 'downtrend' ? '#ff4444' : '#ffaa00',
                }}>
                  {setup.technical_context.trend === 'uptrend' ? '↑' : setup.technical_context.trend === 'downtrend' ? '↓' : '↔'}{' '}
                  {setup.technical_context.trend}
                </span>
              </div>
              <div style={styles.techMAs}>
                <span style={styles.techMA}>
                  MA50 ${setup.technical_context.ma50?.toFixed(2)}
                  <span style={{ color: setup.technical_context.pct_from_ma50 >= 0 ? '#00ffaa' : '#ff4444' }}>
                    {' '}({setup.technical_context.pct_from_ma50 >= 0 ? '+' : ''}{setup.technical_context.pct_from_ma50?.toFixed(1)}%)
                  </span>
                </span>
                <span style={styles.techMA}>
                  MA200 ${setup.technical_context.ma200?.toFixed(2)}
                  <span style={{ color: setup.technical_context.pct_from_ma200 >= 0 ? '#00ffaa' : '#ff4444' }}>
                    {' '}({setup.technical_context.pct_from_ma200 >= 0 ? '+' : ''}{setup.technical_context.pct_from_ma200?.toFixed(1)}%)
                  </span>
                </span>
              </div>
            </div>
          )}

          {/* WHY THIS EXISTS */}
          <div style={styles.section}>
            <div style={styles.sectionTitle}>WHY THIS EXISTS</div>
            <p style={styles.narrative}>{catalyst.catalyst_summary}</p>
          </div>

          <IVRankMeter ivRank={ivRank} />

          {/* THE TRADE */}
          <div style={styles.section}>
            <div style={styles.sectionTitle}>THE TRADE</div>
            <div style={styles.tradeGrid}>
              <span style={styles.tradeLine}>BUY {expiryStr} ${long_strike?.toFixed(0)}C / SELL {expiryStr} ${short_strike?.toFixed(0)}C</span>
              <span />
              <span style={styles.tradeKey}>Net Debit</span>
              <span style={styles.tradeVal}>${net_debit?.toFixed(2)}</span>
              <span style={styles.tradeKey}>Max Gain</span>
              <span style={styles.tradeVal}>${max_gain?.toFixed(2)}</span>
              <span style={styles.tradeKey}>R:R</span>
              <span style={{ ...styles.tradeVal, color: '#00ffaa' }}>{rr_ratio?.toFixed(2)}:1</span>
              <span style={styles.tradeKey}>Breakeven</span>
              <span style={styles.tradeVal}>${breakeven?.toFixed(2)} (+{breakeven_move_pct?.toFixed(1)}%)</span>
              <span style={styles.tradeKey}>Prob. Profit</span>
              <span style={styles.tradeVal}>~{probability_of_profit?.toFixed(0)}%</span>
            </div>
          </div>

          {/* P&L SCENARIOS */}
          <div style={styles.section}>
            <div style={styles.sectionTitle}>P&L SCENARIOS</div>
            <SwingPnL setup={setup} />
          </div>

          {/* LIQUIDITY */}
          <div style={styles.section}>
            <div style={styles.sectionTitle}>LIQUIDITY</div>
            <div style={styles.liqGrid}>
              <span style={styles.tradeKey}>${long_strike?.toFixed(0)}C</span>
              <span style={styles.tradeVal}>OI {long_leg_oi?.toLocaleString()}</span>
              <span style={styles.tradeVal}>Vol {long_leg_volume?.toLocaleString()}</span>
              <span style={{ ...styles.tradeVal, color: long_leg_spread_pct <= 10 ? '#00ffaa' : '#ff4444' }}>
                Spread {long_leg_spread_pct?.toFixed(1)}% {long_leg_spread_pct <= 10 ? '✓' : '✗'}
              </span>
              <span style={styles.tradeKey}>${short_strike?.toFixed(0)}C</span>
              <span style={styles.tradeVal}>OI {short_leg_oi?.toLocaleString()}</span>
              <span style={styles.tradeVal}>—</span>
              <span style={{ ...styles.tradeVal, color: short_leg_spread_pct <= 10 ? '#00ffaa' : '#ff4444' }}>
                Spread {short_leg_spread_pct?.toFixed(1)}% {short_leg_spread_pct <= 10 ? '✓' : '✗'}
              </span>
            </div>
          </div>

          {/* GREEKS */}
          <div style={styles.section}>
            <div style={styles.sectionTitle}>GREEKS</div>
            <div style={styles.greekRow}>
              <span style={styles.tradeKey}>Net Δ</span>
              <span style={styles.tradeVal}>{net_delta?.toFixed(3)}</span>
              <span style={styles.tradeKey}>Net θ</span>
              <span style={styles.tradeVal}>${net_theta?.toFixed(3)}/day</span>
              <span style={styles.tradeKey}>Net ν</span>
              <span style={styles.tradeVal}>${net_vega?.toFixed(3)}</span>
            </div>
          </div>

          {/* ACTIONS */}
          <div style={styles.section}>
            <div style={styles.sectionTitle}>POSITION SIZING</div>
            <div style={styles.sizingPanel}>
              <div style={styles.sizingRow}>
                <span style={styles.sizingLabel}>RISK PER TRADE</span>
                <input
                  type="number" min="0.5" max="10" step="0.5"
                  value={riskPct}
                  onChange={e => {
                    const v = parseFloat(e.target.value)
                    if (!isNaN(v)) {
                      setRiskPct(v)
                      localStorage.setItem('qqq_risk_pct', String(v))
                    }
                  }}
                  style={styles.riskInput}
                />
                <span style={styles.sizingLabel}>% of $40k</span>
              </div>
              <div style={styles.sizingResult}>
                <span style={styles.sizingContracts}>{suggestedContracts} contracts</span>
                <span style={styles.sizingLoss}>(${maxLossDollars.toLocaleString()} max loss)</span>
              </div>
            </div>
          </div>

          <div style={styles.actions}>
            <button style={styles.btn} onClick={() => onSaveToJournal(setup)}>
              SAVE TO JOURNAL
            </button>
            <button style={{ ...styles.btn, ...styles.btnPrimary }} onClick={copyOrder}>
              {copied ? '✓ COPIED!' : 'COPY ORDER STRING'}
            </button>
          </div>

          <div style={styles.orderString}>
            <span style={{ color: '#555', fontSize: '11px', fontFamily: 'monospace' }}>Order: </span>
            <span style={{ color: '#aaa', fontSize: '11px', fontFamily: 'monospace' }}>{order_string}</span>
          </div>

          {/* Signal detail */}
          <div style={styles.signalNote}>
            <span style={{ color: '#555', fontSize: '11px' }}>Signal: </span>
            <span style={{ color: '#888', fontSize: '11px' }}>{signal.description}</span>
          </div>
        </div>
      )}
    </div>
  )
}

const styles = {
  card: {
    background: '#0d0d1a',
    border: '1px solid #1a1a2e',
    borderRadius: '6px',
    margin: '8px 16px',
    overflow: 'hidden',
    transition: 'border-color 0.2s',
  },
  header: {
    display: 'flex',
    justifyContent: 'space-between',
    alignItems: 'center',
    padding: '12px 16px 4px',
    cursor: 'pointer',
  },
  headerLeft: {
    display: 'flex',
    alignItems: 'center',
    gap: '12px',
    flexWrap: 'wrap',
  },
  headerRight: {
    display: 'flex',
    alignItems: 'center',
    gap: '12px',
  },
  symbol: {
    fontFamily: 'monospace',
    fontSize: '18px',
    fontWeight: 'bold',
    color: '#e0e0e0',
  },
  price: {
    fontFamily: 'monospace',
    fontSize: '14px',
    color: '#aaa',
  },
  score: {
    fontFamily: 'monospace',
    fontSize: '13px',
    fontWeight: 'bold',
  },
  scoreLabel: {
    fontFamily: 'monospace',
    fontSize: '10px',
    color: '#666',
    marginLeft: '4px',
    letterSpacing: '0.04em',
  },
  ivr: {
    fontFamily: 'monospace',
    fontSize: '12px',
    color: '#888',
  },
  detector: {
    fontFamily: 'monospace',
    fontSize: '11px',
    color: '#666',
    border: '1px solid #2a2a3e',
    padding: '2px 8px',
    borderRadius: '3px',
  },
  expand: {
    color: '#444',
    fontSize: '12px',
    fontFamily: 'monospace',
  },
  breakdown: {
    background: '#0a0a14',
    border: '1px solid #1a1a2e',
    borderRadius: '3px',
    padding: '6px 10px',
    minWidth: '200px',
    display: 'inline-block',
  },
  breakdownRow: {
    display: 'flex',
    justifyContent: 'space-between',
    gap: '16px',
    fontFamily: 'monospace',
    fontSize: '11px',
    padding: '1px 0',
  },
  breakdownLabel: { color: '#888' },
  breakdownPts: { color: '#00ffaa' },
  subHeader: {
    display: 'flex',
    justifyContent: 'space-between',
    padding: '0 16px 12px',
    cursor: 'pointer',
    flexWrap: 'wrap',
    gap: '8px',
  },
  structure: {
    fontFamily: 'monospace',
    fontSize: '12px',
    color: '#aaa',
  },
  metrics: {
    fontFamily: 'monospace',
    fontSize: '12px',
    color: '#777',
  },
  body: {
    padding: '0 16px 16px',
  },
  divider: {
    border: 'none',
    borderTop: '1px solid #1a1a2e',
    margin: '0 0 12px',
  },
  techBlock: {
    background: '#0a0a14',
    border: '1px solid #1a1a2e',
    borderRadius: '3px',
    padding: '8px 12px',
    marginBottom: '12px',
  },
  techHeader: { display: 'flex', alignItems: 'center', gap: '10px', marginBottom: '6px' },
  techSymbol: { fontFamily: 'monospace', fontSize: '13px', color: '#fff', fontWeight: 'bold' },
  techPrice: { fontFamily: 'monospace', fontSize: '13px', color: '#aaa' },
  techTrend: { fontFamily: 'monospace', fontSize: '12px' },
  techMAs: { display: 'flex', gap: '20px' },
  techMA: { fontFamily: 'monospace', fontSize: '12px', color: '#666' },
  section: {
    marginBottom: '16px',
  },
  sectionTitle: {
    fontFamily: 'monospace',
    fontSize: '10px',
    color: '#555',
    letterSpacing: '0.1em',
    marginBottom: '8px',
  },
  narrative: {
    fontSize: '13px',
    color: '#ccc',
    lineHeight: '1.6',
    maxWidth: '600px',
  },
  tradeGrid: {
    display: 'grid',
    gridTemplateColumns: 'max-content 1fr max-content 1fr max-content 1fr',
    gap: '4px 16px',
    alignItems: 'center',
  },
  tradeLine: {
    fontFamily: 'monospace',
    fontSize: '13px',
    color: '#ddd',
    gridColumn: '1 / -1',
    marginBottom: '4px',
  },
  tradeKey: {
    fontFamily: 'monospace',
    fontSize: '11px',
    color: '#555',
  },
  tradeVal: {
    fontFamily: 'monospace',
    fontSize: '12px',
    color: '#bbb',
  },
  liqGrid: {
    display: 'grid',
    gridTemplateColumns: 'max-content 1fr 1fr 1fr',
    gap: '4px 16px',
  },
  greekRow: {
    display: 'flex',
    gap: '24px',
    flexWrap: 'wrap',
  },
  sizingPanel: {
    background: '#0a0a14',
    border: '1px solid #1a1a2e',
    borderRadius: '3px',
    padding: '8px 12px',
  },
  sizingRow: { display: 'flex', alignItems: 'center', gap: '8px', marginBottom: '4px' },
  sizingLabel: { fontFamily: 'monospace', fontSize: '10px', color: '#555' },
  riskInput: {
    width: '50px',
    background: '#0f0f1a',
    border: '1px solid #2a2a3e',
    color: '#00ffaa',
    fontFamily: 'monospace',
    fontSize: '12px',
    padding: '2px 6px',
    borderRadius: '3px',
    textAlign: 'center',
  },
  sizingResult: { display: 'flex', alignItems: 'baseline', gap: '8px' },
  sizingContracts: { fontFamily: 'monospace', fontSize: '16px', color: '#00ffaa', fontWeight: 'bold' },
  sizingLoss: { fontFamily: 'monospace', fontSize: '11px', color: '#888' },
  actions: {
    display: 'flex',
    gap: '8px',
    margin: '16px 0 8px',
  },
  btn: {
    padding: '8px 16px',
    background: 'none',
    border: '1px solid #333',
    color: '#aaa',
    cursor: 'pointer',
    fontFamily: 'monospace',
    fontSize: '12px',
    borderRadius: '3px',
    letterSpacing: '0.05em',
  },
  btnPrimary: {
    border: '1px solid #00ffaa',
    color: '#00ffaa',
    background: '#0a1a0f',
  },
  orderString: {
    background: '#080810',
    padding: '8px 12px',
    borderRadius: '3px',
    marginBottom: '8px',
    wordBreak: 'break-all',
  },
  signalNote: {
    marginTop: '4px',
  },
}
