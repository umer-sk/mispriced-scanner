import { useState } from 'react'
import SectorPanel from './SectorPanel.jsx'

function trendArrow(dir) {
  if (dir === 'improving') return '▲'
  if (dir === 'deteriorating') return '▼'
  return '—'
}

function tileColor(classification) {
  if (classification === 'bullish') return '#00ffaa'
  if (classification === 'bearish') return '#ff4444'
  return '#555'
}

export default function SectorStrip({ sectors, activeSector, onSectorClick }) {
  const [panelOpen, setPanelOpen] = useState(false)

  if (!sectors || sectors.length === 0) return null

  return (
    <div style={styles.wrapper}>
      <div style={styles.strip}>
        <span style={styles.stripLabel}>SECTORS</span>
        <div style={styles.tiles}>
          {sectors.map(s => {
            const isActive = activeSector === s.etf
            const color = tileColor(s.classification)
            return (
              <button
                key={s.etf}
                style={{
                  ...styles.tile,
                  borderColor: isActive ? color : '#2a2a3e',
                  background: isActive ? (s.classification === 'bullish' ? '#0a1a0f' : s.classification === 'bearish' ? '#1a0a0a' : '#0a0a14') : 'none',
                }}
                onClick={() => onSectorClick(isActive ? null : s.etf)}
              >
                <span style={{ ...styles.etf, color }}>{s.etf}</span>
                <span style={{ ...styles.arrow, color }}>{trendArrow(s.trend_direction)}</span>
                <span style={{ ...styles.ret, color }}>
                  {s.return_vs_spy_4w >= 0 ? '+' : ''}{s.return_vs_spy_4w.toFixed(1)}%
                </span>
              </button>
            )
          })}
        </div>
        <button style={styles.viewAll} onClick={() => setPanelOpen(v => !v)}>
          {panelOpen ? 'Close ✕' : 'View all →'}
        </button>
      </div>

      {panelOpen && <SectorPanel sectors={sectors} />}
    </div>
  )
}

const styles = {
  wrapper: { background: '#080810', borderBottom: '1px solid #1a1a2e' },
  strip: {
    display: 'flex', alignItems: 'center', gap: '8px',
    padding: '6px 16px', overflowX: 'auto',
  },
  stripLabel: {
    fontFamily: 'monospace', fontSize: '10px', color: '#555',
    letterSpacing: '0.08em', whiteSpace: 'nowrap',
  },
  tiles: { display: 'flex', gap: '6px', flex: 1 },
  tile: {
    display: 'flex', alignItems: 'center', gap: '4px',
    padding: '4px 8px', border: '1px solid #2a2a3e',
    cursor: 'pointer', borderRadius: '3px', whiteSpace: 'nowrap',
    background: 'none',
  },
  etf: { fontFamily: 'monospace', fontSize: '11px', fontWeight: 'bold' },
  arrow: { fontFamily: 'monospace', fontSize: '10px' },
  ret: { fontFamily: 'monospace', fontSize: '11px' },
  viewAll: {
    padding: '4px 10px', background: 'none', border: '1px solid #2a2a3e',
    color: '#555', cursor: 'pointer', fontFamily: 'monospace', fontSize: '11px',
    borderRadius: '3px', whiteSpace: 'nowrap', marginLeft: 'auto',
  },
}
