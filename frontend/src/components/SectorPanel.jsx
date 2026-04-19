import { useState } from 'react'

const PERIODS = ['1W', '4W', '12W']

function arrow(dir) {
  if (dir === 'improving') return '▲'
  if (dir === 'deteriorating') return '▼'
  return '—'
}

function retColor(val) {
  return val >= 0 ? '#00ffaa' : '#ff4444'
}

export default function SectorPanel({ sectors }) {
  const [period, setPeriod] = useState('4W')

  const getReturn = (s) => {
    if (period === '1W') return s.return_vs_spy_1w
    if (period === '12W') return s.return_vs_spy_12w
    return s.return_vs_spy_4w
  }

  const sorted = [...sectors].sort((a, b) => getReturn(b) - getReturn(a))

  return (
    <div style={styles.panel}>
      <div style={styles.header}>
        <span style={styles.title}>SECTOR BREAKDOWN</span>
        <div style={styles.periodTabs}>
          <span style={styles.label}>vs SPY:</span>
          {PERIODS.map(p => (
            <button
              key={p}
              style={{ ...styles.tab, ...(period === p ? styles.tabActive : {}) }}
              onClick={() => setPeriod(p)}
            >
              {p}
            </button>
          ))}
        </div>
      </div>

      <div style={styles.table}>
        <div style={styles.headerRow}>
          <span style={{ ...styles.cell, width: 60 }}>ETF</span>
          <span style={{ ...styles.cell, flex: 1 }}>Sector</span>
          <span style={{ ...styles.cell, width: 90, textAlign: 'right' }}>vs SPY ({period})</span>
          <span style={{ ...styles.cell, width: 70, textAlign: 'right' }}>RS Score</span>
          <span style={{ ...styles.cell, width: 60, textAlign: 'center' }}>Trend</span>
        </div>
        {sorted.map(s => {
          const ret = getReturn(s)
          return (
            <div key={s.etf} style={styles.row}>
              <span style={{ ...styles.cell, width: 60, color: '#fff', fontWeight: 'bold' }}>{s.etf}</span>
              <span style={{ ...styles.cell, flex: 1, color: '#888' }}>{s.name}</span>
              <span style={{ ...styles.cell, width: 90, textAlign: 'right', color: retColor(ret) }}>
                {ret >= 0 ? '+' : ''}{ret.toFixed(1)}%
              </span>
              <span style={{ ...styles.cell, width: 70, textAlign: 'right', color: '#aaa' }}>
                {s.rs_score.toFixed(0)}
              </span>
              <span style={{
                ...styles.cell, width: 60, textAlign: 'center',
                color: s.trend_direction === 'improving' ? '#00ffaa' : s.trend_direction === 'deteriorating' ? '#ff4444' : '#555',
              }}>
                {arrow(s.trend_direction)}
              </span>
            </div>
          )
        })}
      </div>
    </div>
  )
}

const styles = {
  panel: { padding: '12px 16px', borderTop: '1px solid #1a1a2e', background: '#080810' },
  header: { display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: '10px' },
  title: { fontFamily: 'monospace', fontSize: '10px', color: '#555', letterSpacing: '0.08em' },
  periodTabs: { display: 'flex', alignItems: 'center', gap: '6px' },
  label: { fontFamily: 'monospace', fontSize: '10px', color: '#555' },
  tab: {
    padding: '3px 8px', background: 'none', border: '1px solid #2a2a3e',
    color: '#666', cursor: 'pointer', fontFamily: 'monospace', fontSize: '11px', borderRadius: '3px',
  },
  tabActive: { borderColor: '#00ffaa', color: '#00ffaa', background: '#0a1a0f' },
  table: { display: 'flex', flexDirection: 'column', gap: '2px' },
  headerRow: {
    display: 'flex', padding: '4px 0',
    borderBottom: '1px solid #1a1a2e', marginBottom: '4px',
  },
  row: {
    display: 'flex', padding: '5px 0', borderBottom: '1px solid #0f0f1a',
  },
  cell: { fontFamily: 'monospace', fontSize: '12px', color: '#666' },
}
