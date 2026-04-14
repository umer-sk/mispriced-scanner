export default function MarketContext({ marketContext }) {
  if (!marketContext) {
    return (
      <div style={styles.neutral}>
        <span style={styles.label}>MARKET</span>
        <span>Loading market conditions...</span>
      </div>
    )
  }

  const { skip_today, skip_reason, market_regime, vix_level, vix_trend, scan_timestamp, market_is_open } = marketContext

  let bg = '#0a1a0f'
  let border = '#00ffaa'
  let color = '#00ffaa'
  let statusText = 'Market conditions normal. Proceed with review.'

  if (market_regime === 'RISK_OFF' || skip_today) {
    bg = '#1a0f00'
    border = '#ffaa00'
    color = '#ffaa00'
    statusText = skip_reason || 'Market conditions unfavorable today.'
  }
  if (market_regime === 'RISK_OFF' && (vix_level || 0) > 35) {
    bg = '#1a0505'
    border = '#ff4444'
    color = '#ff4444'
  }

  const scanTime = scan_timestamp
    ? new Date(scan_timestamp).toLocaleTimeString('en-US', { hour: '2-digit', minute: '2-digit', timeZone: 'America/New_York' })
    : '—'

  return (
    <div style={{ ...styles.banner, background: bg, borderLeft: `4px solid ${border}` }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', flexWrap: 'wrap', gap: '8px' }}>
        <div style={{ flex: 1 }}>
          <div style={{ display: 'flex', gap: '12px', alignItems: 'center', marginBottom: '4px' }}>
            <span style={{ ...styles.label, color }}>
              {skip_today ? '⚠ MARKET CONTEXT' : '● MARKET CONTEXT'}
            </span>
            <span style={{ ...styles.badge, color, border: `1px solid ${color}` }}>
              {market_regime}
            </span>
            <span style={{ color: '#666', fontSize: '12px' }}>
              {market_is_open ? '● OPEN' : '○ CLOSED'}
            </span>
          </div>
          <p style={{ color, fontSize: '13px', lineHeight: '1.5', maxWidth: '800px' }}>
            {statusText}
          </p>
        </div>
        <div style={{ textAlign: 'right', fontSize: '11px', color: '#555', whiteSpace: 'nowrap' }}>
          <div>QQQ IV: {vix_level ? `${vix_level.toFixed(1)}%` : '—'} {vix_trend ? `(${vix_trend.toLowerCase()})` : ''}</div>
          <div>Scan: {scanTime} ET</div>
        </div>
      </div>
    </div>
  )
}

const styles = {
  banner: {
    padding: '12px 16px',
    borderRadius: '4px',
    margin: '12px 16px',
  },
  neutral: {
    padding: '10px 16px',
    background: '#0a1a0f',
    borderLeft: '4px solid #00ffaa',
    margin: '12px 16px',
    borderRadius: '4px',
    color: '#00ffaa',
    fontSize: '13px',
  },
  label: {
    fontFamily: 'monospace',
    fontSize: '11px',
    fontWeight: 'bold',
    letterSpacing: '0.1em',
  },
  badge: {
    fontFamily: 'monospace',
    fontSize: '10px',
    padding: '2px 6px',
    borderRadius: '2px',
    letterSpacing: '0.05em',
  },
}
