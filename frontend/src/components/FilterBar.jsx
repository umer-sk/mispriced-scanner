const DETECTORS = [
  { key: 'all',            label: 'All' },
  { key: 'iv_rank',        label: 'IV Rank' },
  { key: 'skew',           label: 'Skew' },
  { key: 'parity',         label: 'Parity' },
  { key: 'term',           label: 'Term' },
  { key: 'move',           label: 'Move' },
  { key: 'put_iv_rank',    label: 'Put IV Rank' },
  { key: 'skew_inversion', label: 'Skew Inv.' },
  { key: 'put_parity',     label: 'Put Parity' },
  { key: 'downside_move',  label: 'Downside' },
]

const DIRECTIONS = [
  { key: 'both',    label: 'Both' },
  { key: 'bullish', label: '▲ Bullish' },
  { key: 'bearish', label: '▼ Bearish' },
]

const SORTS = [
  { key: 'score', label: 'Score' },
  { key: 'rr', label: 'R:R' },
  { key: 'debit', label: 'Debit' },
  { key: 'symbol', label: 'Symbol' },
]

export default function FilterBar({ filters, onChange }) {
  const set = (key, value) => onChange({ ...filters, [key]: value })

  return (
    <div style={styles.container}>
      {/* Direction toggle */}
      <div style={styles.row}>
        <span style={styles.label}>DIRECTION</span>
        <div style={styles.tabs}>
          {DIRECTIONS.map(d => (
            <button
              key={d.key}
              style={{
                ...styles.tab,
                ...(filters.direction === d.key ? {
                  ...styles.tabActive,
                  ...(d.key === 'bearish' ? { borderColor: '#ff4444', color: '#ff4444', background: '#1a0a0a' } : {}),
                } : {}),
              }}
              onClick={() => set('direction', d.key)}
            >
              {d.label}
            </button>
          ))}
        </div>
      </div>

      {/* Detector tabs */}
      <div style={styles.row}>
        <span style={styles.label}>FILTER</span>
        <div style={styles.tabs}>
          {DETECTORS.map(d => (
            <button
              key={d.key}
              style={{ ...styles.tab, ...(filters.detector === d.key ? styles.tabActive : {}) }}
              onClick={() => set('detector', d.key)}
            >
              {d.label}
            </button>
          ))}
        </div>
      </div>

      {/* Sort */}
      <div style={styles.row}>
        <span style={styles.label}>SORT</span>
        {SORTS.map(s => (
          <button
            key={s.key}
            style={{ ...styles.tab, ...(filters.sort === s.key ? styles.tabActive : {}) }}
            onClick={() => set('sort', s.key)}
          >
            {s.label} {filters.sort === s.key ? '▼' : ''}
          </button>
        ))}
      </div>
    </div>
  )
}

const styles = {
  container: {
    background: '#0a0a14',
    borderBottom: '1px solid #1a1a2e',
    padding: '8px 16px',
    display: 'flex',
    flexDirection: 'column',
    gap: '8px',
  },
  row: {
    display: 'flex',
    alignItems: 'center',
    gap: '8px',
    flexWrap: 'wrap',
  },
  label: {
    fontFamily: 'monospace',
    fontSize: '10px',
    color: '#555',
    letterSpacing: '0.08em',
    whiteSpace: 'nowrap',
  },
  tabs: {
    display: 'flex',
    gap: '4px',
    flexWrap: 'wrap',
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
    background: '#0a1a0f',
  },
}
