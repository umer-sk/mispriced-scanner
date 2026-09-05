import { useState, useEffect } from 'react'
import { fetchForwardTest, fetchForwardTestPositions } from '../api.js'

function StatRow({ label, s }) {
  const hasData = !!s.n
  return (
    <tr>
      <td style={styles.td}>{label}</td>
      <td style={styles.tdNum}>{s.n}</td>
      <td style={{ ...styles.tdNum, color: !hasData ? '#555' : (s.win_rate >= 50 ? '#00ffaa' : '#ff4444') }}>
        {hasData ? `${s.win_rate}%` : '—'}
      </td>
      <td style={{ ...styles.tdNum, color: !hasData ? '#555' : (s.avg_pnl >= 0 ? '#00ffaa' : '#ff4444') }}>
        {hasData ? `${s.avg_pnl > 0 ? '+' : ''}${s.avg_pnl}%` : '—'}
      </td>
      <td style={styles.tdNum}>{hasData ? `+${s.avg_mfe}%` : '—'}</td>
      <td style={styles.tdNum}>{hasData ? `${s.avg_mae}%` : '—'}</td>
    </tr>
  )
}

export default function ForwardTest() {
  const [stats, setStats] = useState(null)
  const [positions, setPositions] = useState([])
  const [error, setError] = useState(null)
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    let cancelled = false
    Promise.all([fetchForwardTest(), fetchForwardTestPositions()])
      .then(([s, p]) => {
        if (cancelled) return
        setStats(s)
        setPositions(p.positions || [])
      })
      .catch(e => { if (!cancelled) setError(e.message) })
      .finally(() => { if (!cancelled) setLoading(false) })
    return () => { cancelled = true }
  }, [])

  if (loading) return <div style={styles.note}>Loading…</div>
  if (error) return <div style={styles.error}>Could not load forward test: {error}</div>
  if (!stats || stats.closed_count === 0) {
    return (
      <div style={styles.note}>
        No resolved positions yet. {stats?.open_count ?? 0} open.
        Results appear as positions hit a target, stop, or expiry.
      </div>
    )
  }

  return (
    <div style={styles.wrap}>
      {stats.truncated && (
        <div style={styles.warning}>
          Truncated: this aggregate is computed over the most recent {stats.rows_fetched} position
          rows only — the oldest positions are excluded and not reflected in these stats.
        </div>
      )}

      <div style={styles.caveat}>
        Entry is booked at the natural (long ask − short bid) while every exit
        is marked on mid, so each position starts about one round-trip
        half-spread under water — these numbers understate the raw signal, more
        so on wider spreads. {stats.open_count} still open
        {stats.unpriceable_count > 0 && `, ${stats.unpriceable_count} unpriceable`}.
      </div>

      <table style={styles.table}>
        <thead>
          <tr>
            <th style={styles.th}>GROUP</th><th style={styles.thNum}>N</th>
            <th style={styles.thNum}>WIN%</th><th style={styles.thNum}>AVG P&L</th>
            <th style={styles.thNum}>AVG MFE</th><th style={styles.thNum}>AVG MAE</th>
          </tr>
        </thead>
        <tbody>
          <StatRow label="ALL" s={stats.overall} />
          {Object.entries(stats.by_tier).map(([k, s]) => <StatRow key={`t${k}`} label={`Tier ${k}`} s={s} />)}
          {Object.entries(stats.by_detector).map(([k, s]) => <StatRow key={`d${k}`} label={k} s={s} />)}
          {Object.entries(stats.by_source).map(([k, s]) => <StatRow key={`src${k}`} label={k} s={s} />)}
        </tbody>
      </table>

      <div style={styles.caveat}>
        N is the sample size. Treat any row with a small N as directional only.
      </div>

      <table style={styles.table}>
        <thead>
          <tr>
            <th style={styles.th}>SYMBOL</th><th style={styles.th}>DETECTOR</th>
            <th style={styles.th}>TIER</th><th style={styles.th}>STATUS</th>
            <th style={styles.thNum}>ENTRY</th><th style={styles.thNum}>P&L</th>
            <th style={styles.thNum}>MFE</th><th style={styles.thNum}>MAE</th>
          </tr>
        </thead>
        <tbody>
          {positions.map(p => (
            <tr key={p.id}>
              <td style={styles.td}>{p.symbol}</td>
              <td style={styles.td}>{p.detector || p.source}</td>
              <td style={styles.td}>{p.tier}</td>
              <td style={styles.td}>{p.status}</td>
              <td style={styles.tdNum}>
                {p.entry_debit == null ? '—' : `$${Number(p.entry_debit).toFixed(2)}`}
              </td>
              <td style={{
                ...styles.tdNum,
                color: p.realized_pnl_pct == null ? '#555' : (p.realized_pnl_pct >= 0 ? '#00ffaa' : '#ff4444'),
              }}>
                {p.realized_pnl_pct == null ? '—' : `${p.realized_pnl_pct > 0 ? '+' : ''}${p.realized_pnl_pct}%`}
              </td>
              <td style={styles.tdNum}>{p.mfe_pct == null ? '—' : `+${p.mfe_pct}%`}</td>
              <td style={styles.tdNum}>{p.mae_pct == null ? '—' : `${p.mae_pct}%`}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

const styles = {
  wrap: { padding: '16px' },
  table: { width: '100%', borderCollapse: 'collapse', fontFamily: 'monospace',
           fontSize: '12px', marginBottom: '20px' },
  th: { textAlign: 'left', color: '#555', borderBottom: '1px solid #1a1a2e', padding: '6px' },
  thNum: { textAlign: 'right', color: '#555', borderBottom: '1px solid #1a1a2e', padding: '6px' },
  td: { padding: '6px', color: '#ccc', borderBottom: '1px solid #111' },
  tdNum: { padding: '6px', textAlign: 'right', color: '#ccc', borderBottom: '1px solid #111' },
  note: { padding: '32px 16px', color: '#555', fontFamily: 'monospace', fontSize: '13px' },
  error: { padding: '16px', color: '#ff4444', fontFamily: 'monospace', fontSize: '13px' },
  caveat: { color: '#555', fontFamily: 'monospace', fontSize: '11px', marginBottom: '12px' },
  warning: {
    background: '#1a1000',
    borderLeft: '4px solid #ffaa00',
    color: '#ffaa00',
    padding: '8px 16px',
    fontSize: '12px',
    fontFamily: 'monospace',
    marginBottom: '12px',
  },
}
