import { useState } from 'react'
import OpportunityCard from './OpportunityCard.jsx'

const DETECTOR_LABELS = {
  iv_rank: 'IV Rank',
  skew: 'Skew',
  parity: 'Parity',
  term: 'Term',
  move: 'Move',
}

function scoreColor(score) {
  if (score >= 75) return '#00ffaa'
  if (score >= 55) return '#ffaa00'
  return '#ff4444'
}

const COLS = [
  {
    key: 'symbol',
    label: 'SYMBOL',
    align: 'left',
    get: s => s.symbol,
    fmt: v => v,
  },
  {
    key: 'stock_price',
    label: 'PRICE',
    get: s => s.stock_price,
    fmt: v => `$${v?.toFixed(2)}`,
  },
  {
    key: 'score',
    label: 'SCORE',
    get: s => s.score,
    fmt: (v, s) => (
      <span style={{ color: scoreColor(v), fontWeight: 'bold' }}>{v}</span>
    ),
  },
  {
    key: 'detector',
    label: 'SIGNAL',
    get: s => s.signal.detector,
    fmt: v => (
      <span style={styles.badge}>{DETECTOR_LABELS[v] || v}</span>
    ),
  },
  {
    key: 'iv_rank',
    label: 'IVR%',
    get: s => s.signal.raw_data?.iv_rank ?? 0,
    fmt: v => `${v?.toFixed(0)}%`,
  },
  {
    key: 'rr_ratio',
    label: 'R:R',
    get: s => s.rr_ratio,
    fmt: v => `${v?.toFixed(2)}:1`,
  },
  {
    key: 'net_debit',
    label: 'DEBIT',
    get: s => s.net_debit,
    fmt: v => `$${v?.toFixed(2)}`,
  },
  {
    key: 'expiry',
    label: 'EXPIRY',
    get: s => s.expiry,
    fmt: v => v
      ? new Date(v + 'T00:00:00').toLocaleDateString('en-US', { month: 'short', day: 'numeric' })
      : '—',
  },
  {
    key: 'dte',
    label: 'DTE',
    get: s => s.dte,
    fmt: v => v,
  },
  {
    key: 'breakeven_move_pct',
    label: 'BE MOVE',
    get: s => s.breakeven_move_pct,
    fmt: v => `+${v?.toFixed(1)}%`,
  },
  {
    key: 'probability_of_profit',
    label: 'PROB%',
    get: s => s.probability_of_profit,
    fmt: v => `~${v?.toFixed(0)}%`,
  },
]

export default function OpportunityTable({ opportunities, onSaveToJournal }) {
  const [sortCol, setSortCol] = useState('score')
  const [sortDir, setSortDir] = useState('desc')
  const [expandedIdx, setExpandedIdx] = useState(null)

  function handleSort(key) {
    if (sortCol === key) {
      setSortDir(d => d === 'desc' ? 'asc' : 'desc')
    } else {
      setSortCol(key)
      setSortDir('desc')
    }
    setExpandedIdx(null)
  }

  function toggleRow(i) {
    setExpandedIdx(idx => idx === i ? null : i)
  }

  const col = COLS.find(c => c.key === sortCol) ?? COLS[0]
  const sorted = [...opportunities].sort((a, b) => {
    const av = col.get(a)
    const bv = col.get(b)
    if (av == null) return 1
    if (bv == null) return -1
    const cmp = typeof av === 'string' ? av.localeCompare(bv) : av - bv
    return sortDir === 'asc' ? cmp : -cmp
  })

  if (opportunities.length === 0) {
    return (
      <div style={styles.empty}>
        No opportunities match your filters. Try lowering the minimum score or R:R.
      </div>
    )
  }

  return (
    <div style={styles.wrapper}>
      <div style={styles.scroll}>
        <table style={styles.table}>
          <thead>
            <tr>
              {COLS.map(c => (
                <th
                  key={c.key}
                  style={{
                    ...styles.th,
                    textAlign: c.align ?? 'right',
                    color: sortCol === c.key ? '#00ffaa' : '#555',
                    whiteSpace: 'nowrap',
                  }}
                  onClick={() => handleSort(c.key)}
                >
                  {c.label}
                  {sortCol === c.key && (
                    <span style={{ marginLeft: '4px', fontSize: '10px' }}>
                      {sortDir === 'desc' ? '▼' : '▲'}
                    </span>
                  )}
                </th>
              ))}
              <th style={{ ...styles.th, color: '#555' }}>ACTION</th>
            </tr>
          </thead>
          <tbody>
            {sorted.map((setup, i) => (
              <>
                <tr
                  key={`${setup.symbol}-${setup.signal.detector}-${i}`}
                  style={{
                    ...styles.row,
                    background: expandedIdx === i ? '#0d0d1a' : (i % 2 === 0 ? '#070710' : '#030308'),
                    borderBottom: expandedIdx === i ? 'none' : '1px solid #0f0f1f',
                  }}
                  onClick={() => toggleRow(i)}
                >
                  {COLS.map(c => {
                    const val = c.get(setup)
                    return (
                      <td
                        key={c.key}
                        style={{
                          ...styles.td,
                          textAlign: c.align ?? 'right',
                          fontWeight: c.key === 'symbol' ? 'bold' : 'normal',
                          color: c.key === 'symbol' ? '#e0e0e0' : '#aaa',
                        }}
                      >
                        {typeof c.fmt(val, setup) === 'object'
                          ? c.fmt(val, setup)
                          : <span>{c.fmt(val, setup)}</span>
                        }
                      </td>
                    )
                  })}
                  <td style={{ ...styles.td, textAlign: 'center' }}>
                    <button
                      style={styles.saveBtn}
                      onClick={e => { e.stopPropagation(); onSaveToJournal(setup) }}
                    >
                      + JOURNAL
                    </button>
                  </td>
                </tr>

                {expandedIdx === i && (
                  <tr key={`${i}-expanded`}>
                    <td colSpan={COLS.length + 1} style={{ padding: 0, borderBottom: '1px solid #1a1a2e' }}>
                      <OpportunityCard
                        setup={setup}
                        onSaveToJournal={onSaveToJournal}
                        defaultExpanded
                      />
                    </td>
                  </tr>
                )}
              </>
            ))}
          </tbody>
        </table>
      </div>
      <div style={styles.footer}>
        {sorted.length} row{sorted.length !== 1 ? 's' : ''} · click any row to expand
      </div>
    </div>
  )
}

const styles = {
  wrapper: {
    margin: '0 16px 32px',
  },
  scroll: {
    overflowX: 'auto',
  },
  table: {
    width: '100%',
    borderCollapse: 'collapse',
    fontFamily: 'monospace',
    fontSize: '12px',
    tableLayout: 'auto',
  },
  th: {
    padding: '8px 12px',
    fontSize: '10px',
    letterSpacing: '0.08em',
    cursor: 'pointer',
    borderBottom: '1px solid #1a1a2e',
    background: '#0a0a14',
    userSelect: 'none',
    whiteSpace: 'nowrap',
  },
  row: {
    cursor: 'pointer',
    transition: 'background 0.1s',
  },
  td: {
    padding: '8px 12px',
    fontFamily: 'monospace',
    fontSize: '12px',
    whiteSpace: 'nowrap',
  },
  badge: {
    border: '1px solid #2a2a3e',
    padding: '1px 6px',
    borderRadius: '3px',
    color: '#888',
    fontSize: '10px',
  },
  saveBtn: {
    background: 'none',
    border: '1px solid #2a2a3e',
    color: '#666',
    cursor: 'pointer',
    fontFamily: 'monospace',
    fontSize: '10px',
    padding: '3px 8px',
    borderRadius: '3px',
    whiteSpace: 'nowrap',
  },
  empty: {
    padding: '48px 16px',
    textAlign: 'center',
    color: '#555',
    fontFamily: 'monospace',
    fontSize: '13px',
  },
  footer: {
    padding: '6px 0',
    fontSize: '11px',
    color: '#444',
    fontFamily: 'monospace',
    textAlign: 'right',
  },
}
