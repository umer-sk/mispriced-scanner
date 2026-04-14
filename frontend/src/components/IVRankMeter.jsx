export default function IVRankMeter({ ivRank }) {
  const pct = Math.max(0, Math.min(100, ivRank || 0))

  let color = '#00ffaa'
  let label = 'near annual floor'
  if (pct > 50) {
    color = '#ff4444'
    label = 'elevated'
  } else if (pct > 25) {
    color = '#ffaa00'
    label = 'moderate'
  }

  return (
    <div style={styles.container}>
      <div style={styles.header}>
        <span style={{ color: '#888', fontSize: '11px', fontFamily: 'monospace' }}>52-WEEK IV RANGE</span>
        <span style={{ color, fontFamily: 'monospace', fontSize: '12px', fontWeight: 'bold' }}>
          {pct.toFixed(0)}% — {label}
        </span>
      </div>
      <div style={styles.track}>
        <div style={{ ...styles.fill, width: `${pct}%`, background: color }} />
        <div style={{ ...styles.marker, left: `${pct}%`, borderColor: color }} />
      </div>
      <div style={styles.labels}>
        <span>LOW</span>
        <span>HIGH</span>
      </div>
    </div>
  )
}

const styles = {
  container: {
    margin: '8px 0',
  },
  header: {
    display: 'flex',
    justifyContent: 'space-between',
    marginBottom: '4px',
  },
  track: {
    position: 'relative',
    height: '8px',
    background: '#1a1a2e',
    borderRadius: '4px',
    overflow: 'visible',
  },
  fill: {
    height: '100%',
    borderRadius: '4px',
    transition: 'width 0.3s ease',
    opacity: 0.7,
  },
  marker: {
    position: 'absolute',
    top: '-3px',
    width: '2px',
    height: '14px',
    borderLeft: '2px solid',
    transform: 'translateX(-1px)',
    transition: 'left 0.3s ease',
  },
  labels: {
    display: 'flex',
    justifyContent: 'space-between',
    marginTop: '3px',
    fontSize: '10px',
    color: '#444',
    fontFamily: 'monospace',
  },
}
