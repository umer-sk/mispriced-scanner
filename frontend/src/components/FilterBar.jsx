const DETECTORS = [
  { key: 'all', label: 'All' },
  { key: 'iv_rank', label: 'IV Rank' },
  { key: 'skew', label: 'Skew' },
  { key: 'parity', label: 'Parity' },
  { key: 'term', label: 'Term Structure' },
  { key: 'move', label: 'Move' },
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

      {/* Sliders */}
      <div style={styles.row}>
        <span style={styles.label}>MIN R:R</span>
        <input
          type="range" min="1.5" max="5" step="0.1"
          value={filters.minRR}
          onChange={e => set('minRR', parseFloat(e.target.value))}
          style={styles.slider}
        />
        <span style={styles.sliderVal}>{filters.minRR.toFixed(1)}:1</span>

        <span style={{ ...styles.label, marginLeft: '16px' }}>MAX DEBIT</span>
        <input
          type="range" min="1" max="15" step="0.5"
          value={filters.maxDebit}
          onChange={e => set('maxDebit', parseFloat(e.target.value))}
          style={styles.slider}
        />
        <span style={styles.sliderVal}>${filters.maxDebit.toFixed(0)}</span>

        <span style={{ ...styles.label, marginLeft: '16px' }}>MIN SCORE</span>
        <input
          type="range" min="0" max="100" step="5"
          value={filters.minScore}
          onChange={e => set('minScore', parseInt(e.target.value))}
          style={styles.slider}
        />
        <span style={styles.sliderVal}>{filters.minScore}</span>
      </div>

      {/* Sort + OI toggle */}
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
        <label style={styles.toggle}>
          <input
            type="checkbox"
            checked={filters.minOI}
            onChange={e => set('minOI', e.target.checked)}
            style={{ marginRight: '6px' }}
          />
          <span style={{ color: filters.minOI ? '#00ffaa' : '#666' }}>OI &gt; 500</span>
        </label>
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
  slider: {
    width: '100px',
    accentColor: '#00ffaa',
    cursor: 'pointer',
  },
  sliderVal: {
    fontFamily: 'monospace',
    fontSize: '12px',
    color: '#00ffaa',
    minWidth: '40px',
  },
  toggle: {
    display: 'flex',
    alignItems: 'center',
    cursor: 'pointer',
    fontFamily: 'monospace',
    fontSize: '11px',
    marginLeft: '8px',
  },
}
