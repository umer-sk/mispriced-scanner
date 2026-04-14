function PnLRow({ scenario, maxAbsPnl }) {
  const { label, pnl, pnl_pct } = scenario
  const isPos = pnl >= 0
  const color = isPos ? '#00ffaa' : '#ff4444'
  const barWidth = maxAbsPnl > 0 ? Math.min(100, (Math.abs(pnl) / maxAbsPnl) * 100) : 0

  return (
    <div style={styles.row}>
      <span style={styles.rowLabel}>{label}</span>
      <div style={styles.barTrack}>
        <div
          style={{
            ...styles.bar,
            width: `${barWidth}%`,
            background: color,
            opacity: 0.6,
          }}
        />
      </div>
      <span style={{ ...styles.pnlValue, color }}>
        {isPos ? '+' : ''}{pnl_pct.toFixed(0)}%
      </span>
      <span style={{ ...styles.pnlDollar, color }}>
        {isPos ? '+' : ''}${Math.abs(pnl).toFixed(0)}
      </span>
    </div>
  )
}

function ScenarioTable({ title, scenarios }) {
  if (!scenarios || scenarios.length === 0) return null
  const maxAbsPnl = Math.max(...scenarios.map(s => Math.abs(s.pnl)))

  return (
    <div style={styles.table}>
      <div style={styles.tableTitle}>{title}</div>
      {scenarios.map((s, i) => (
        <PnLRow key={i} scenario={s} maxAbsPnl={maxAbsPnl} />
      ))}
    </div>
  )
}

export default function SwingPnL({ setup }) {
  const [view, setView] = useState('5d')
  const scenarios = {
    '5d': setup.scenarios_5d,
    '10d': setup.scenarios_10d,
    'expiry': setup.scenarios_expiry,
  }

  return (
    <div>
      <div style={styles.tabs}>
        {['5d', '10d', 'expiry'].map(t => (
          <button
            key={t}
            style={{ ...styles.tab, ...(view === t ? styles.tabActive : {}) }}
            onClick={() => setView(t)}
          >
            {t === 'expiry' ? `AT EXPIRY (${setup.dte}d)` : `HOLD ${t.toUpperCase()}`}
          </button>
        ))}
      </div>
      <ScenarioTable
        title={view === 'expiry' ? `At expiry — DTE ${setup.dte}` : `If held ${view}`}
        scenarios={scenarios[view]}
      />
    </div>
  )
}

import { useState } from 'react'

const styles = {
  tabs: {
    display: 'flex',
    gap: '4px',
    marginBottom: '8px',
  },
  tab: {
    padding: '4px 10px',
    background: 'none',
    border: '1px solid #2a2a3e',
    color: '#666',
    cursor: 'pointer',
    fontFamily: 'monospace',
    fontSize: '11px',
    borderRadius: '3px',
  },
  tabActive: {
    border: '1px solid #00ffaa',
    color: '#00ffaa',
  },
  table: {
    background: '#0a0a14',
    borderRadius: '4px',
    padding: '8px',
  },
  tableTitle: {
    fontSize: '11px',
    color: '#555',
    fontFamily: 'monospace',
    marginBottom: '6px',
    letterSpacing: '0.05em',
  },
  row: {
    display: 'grid',
    gridTemplateColumns: '130px 1fr 60px 70px',
    alignItems: 'center',
    gap: '8px',
    padding: '3px 0',
  },
  rowLabel: {
    fontSize: '11px',
    color: '#888',
    fontFamily: 'monospace',
  },
  barTrack: {
    height: '10px',
    background: '#111',
    borderRadius: '2px',
    overflow: 'hidden',
  },
  bar: {
    height: '100%',
    borderRadius: '2px',
    transition: 'width 0.2s',
  },
  pnlValue: {
    fontSize: '12px',
    fontFamily: 'monospace',
    textAlign: 'right',
  },
  pnlDollar: {
    fontSize: '11px',
    fontFamily: 'monospace',
    color: '#666',
    textAlign: 'right',
  },
}
