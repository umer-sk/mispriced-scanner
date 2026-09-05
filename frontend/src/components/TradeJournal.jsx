import { useState, useEffect } from 'react'

function daysHeld(entryDate) {
  const start = new Date(entryDate)
  const now = new Date()
  return Math.floor((now - start) / 86400000)
}

// Every field Dashboard.confirmSave writes. The export header used to be a
// hand-maintained subset that had drifted: it named `dte` and `date_saved`,
// neither of which is ever written (so both exported empty), while
// entry_date / total_cost / thesis / score_at_entry were written but never
// exported — silently lost on any export -> import round trip.
const TRADE_FIELDS = [
  'id', 'symbol', 'structure', 'entry_date', 'entry_debit', 'contracts',
  'total_cost', 'thesis', 'score_at_entry', 'status', 'exit_date',
  'exit_credit', 'pnl_dollars', 'pnl_pct', 'notes',
]

// A row must have these to render without throwing.
const REQUIRED_FIELDS = ['id', 'symbol', 'entry_debit']

/** Split one CSV line, respecting quoted fields.
 *
 * export writes each value with JSON.stringify, so any value containing a
 * comma is emitted as a single quoted field. The old importer used
 * line.split(','), which shattered such a row and shifted every column after
 * it — a note like "bought early, added later" silently moved the thesis into
 * `status` and so on down the row.
 */
function splitCsvLine(line) {
  const out = []
  let cur = ''
  let inQuotes = false
  for (let i = 0; i < line.length; i++) {
    const ch = line[i]
    if (inQuotes) {
      if (ch === '\\' && i + 1 < line.length) {   // \" and \\ inside a JSON string
        cur += ch + line[++i]
      } else if (ch === '"') {
        inQuotes = false
        cur += ch
      } else {
        cur += ch
      }
    } else if (ch === '"') {
      inQuotes = true
      cur += ch
    } else if (ch === ',') {
      out.push(cur)
      cur = ''
    } else {
      cur += ch
    }
  }
  out.push(cur)
  return out
}

function parseCsv(text) {
  const lines = text.trim().split(/\r?\n/).filter(l => l.trim() !== '')
  if (lines.length < 2) return { rows: [], skipped: 0 }

  const headers = splitCsvLine(lines[0]).map(h => {
    try { return JSON.parse(h) } catch { return h }
  })

  const rows = []
  let skipped = 0
  for (const line of lines.slice(1)) {
    const vals = splitCsvLine(line)
    if (vals.length !== headers.length) { skipped++; continue }
    const row = {}
    headers.forEach((h, i) => {
      let v = vals[i]
      try { v = JSON.parse(v) } catch { /* leave as the raw string */ }
      row[h] = v === '' ? null : v
    })
    // Tolerant of older exports, which are missing four of the fields.
    if (!REQUIRED_FIELDS.every(f => row[f] !== undefined && row[f] !== null)) {
      skipped++
      continue
    }
    for (const f of TRADE_FIELDS) if (!(f in row)) row[f] = null
    rows.push(row)
  }
  return { rows, skipped }
}

export default function TradeJournal() {
  const [trades, setTrades] = useState([])
  const [closeTarget, setCloseTarget] = useState(null)
  const [exitCredit, setExitCredit] = useState('')
  const [importMsg, setImportMsg] = useState(null)

  useEffect(() => {
    const stored = JSON.parse(localStorage.getItem('qqq_journal') || '[]')
    setTrades(stored)
  }, [])

  function save(updated) {
    // setItem can throw (quota, private browsing). Write first so a failure
    // does not leave the UI showing state that was never persisted.
    localStorage.setItem('qqq_journal', JSON.stringify(updated))
    setTrades(updated)
  }

  function closeTrade() {
    const credit = parseFloat(exitCredit)
    if (isNaN(credit) || credit < 0) return
    const updated = trades.map(t => {
      if (t.id !== closeTarget.id) return t
      const pnl_dollars = Math.round((credit - t.entry_debit) * t.contracts * 100)
      const pnl_pct = ((credit - t.entry_debit) / t.entry_debit) * 100
      return {
        ...t,
        status: 'CLOSED',
        exit_date: new Date().toISOString().split('T')[0],
        exit_credit: credit,
        pnl_dollars,
        pnl_pct: Math.round(pnl_pct),
      }
    })
    save(updated)
    setCloseTarget(null)
    setExitCredit('')
  }

  function deleteTrade(id) {
    save(trades.filter(t => t.id !== id))
  }

  const open = trades.filter(t => t.status === 'OPEN')
  const closed = trades.filter(t => t.status !== 'OPEN')

  const wins = closed.filter(t => (t.pnl_dollars || 0) > 0).length
  const winRate = closed.length > 0 ? Math.round((wins / closed.length) * 100) : null
  const avgPnlPct = closed.length > 0
    ? Math.round(closed.reduce((sum, t) => sum + (t.pnl_pct || 0), 0) / closed.length)
    : null

  function exportCSV() {
    // Union of the canonical fields and anything actually present, so a field
    // added to the writer is exported even before TRADE_FIELDS catches up —
    // the drift that lost four fields in the first place.
    const extra = [...new Set(trades.flatMap(Object.keys))]
      .filter(k => !TRADE_FIELDS.includes(k))
    const headers = [...TRADE_FIELDS, ...extra]
    const rows = trades.map(t =>
      headers.map(h => JSON.stringify(t[h] ?? '')).join(',')
    )
    const csv = [headers.join(','), ...rows].join('\n')
    const blob = new Blob([csv], { type: 'text/csv' })
    const url = URL.createObjectURL(blob)
    const a = document.createElement('a')
    a.href = url
    a.download = `trades_${new Date().toISOString().split('T')[0]}.csv`
    a.click()
    URL.revokeObjectURL(url)
  }

  function importCSV(e) {
    const file = e.target.files[0]
    if (!file) return
    const reader = new FileReader()
    reader.onload = ev => {
      // Parse and validate BEFORE persisting. The old version wrote straight
      // to localStorage inside this handler, so a malformed file was stored
      // first and only then blew up rendering — leaving the tab blank across
      // reloads, with the real history unreachable without devtools.
      let parsed
      try {
        parsed = parseCsv(String(ev.target.result))
      } catch (err) {
        setImportMsg({ error: true, text: `Could not read that file: ${err.message}` })
        return
      }
      const { rows, skipped } = parsed
      if (rows.length === 0) {
        setImportMsg({
          error: true,
          text: skipped
            ? `No usable rows — ${skipped} row(s) could not be parsed. Nothing was changed.`
            : 'No rows found in that file. Nothing was changed.',
        })
        return
      }
      // Merge: an imported row replaces the stored row with the same id;
      // everything else is kept.
      const replaced = trades.filter(t => rows.some(r => r.id === t.id)).length
      const existing = trades.filter(t => !rows.some(r => r.id === t.id))
      try {
        save([...existing, ...rows])
      } catch (err) {
        setImportMsg({ error: true, text: `Could not save: ${err.message}` })
        return
      }
      setImportMsg({
        error: false,
        text: `Imported ${rows.length} trade(s)`
          + (replaced ? `, ${replaced} updated` : '')
          + (skipped ? ` — ${skipped} row(s) skipped as unreadable` : ''),
      })
    }
    reader.onerror = () => setImportMsg({ error: true, text: 'Could not read that file.' })
    reader.readAsText(file)
    e.target.value = ''
  }

  return (
    <div style={{ padding: '16px', maxWidth: '900px' }}>
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: '16px' }}>
        <div style={styles.heading}>MY TRADES</div>
        <div style={{ display: 'flex', gap: '8px' }}>
          <button style={styles.csvBtn} onClick={exportCSV}>Export CSV</button>
          <label style={styles.csvBtn}>
            Import CSV
            <input type="file" accept=".csv" onChange={importCSV} style={{ display: 'none' }} />
          </label>
        </div>
      </div>

      {importMsg && (
        <div
          style={{ ...styles.importMsg, ...(importMsg.error ? styles.importMsgError : {}) }}
          onClick={() => setImportMsg(null)}
          title="Click to dismiss"
        >
          {importMsg.text}
        </div>
      )}

      {/* Stats */}
      {closed.length > 0 && (
        <div style={styles.statsRow}>
          <div style={styles.stat}>
            <span style={styles.statLabel}>Total Trades</span>
            <span style={styles.statVal}>{trades.length}</span>
          </div>
          <div style={styles.stat}>
            <span style={styles.statLabel}>Win Rate</span>
            <span style={{ ...styles.statVal, color: winRate >= 50 ? '#00ffaa' : '#ff4444' }}>
              {winRate}%
            </span>
          </div>
          <div style={styles.stat}>
            <span style={styles.statLabel}>Avg P&L</span>
            <span style={{ ...styles.statVal, color: avgPnlPct >= 0 ? '#00ffaa' : '#ff4444' }}>
              {avgPnlPct >= 0 ? '+' : ''}{avgPnlPct}%
            </span>
          </div>
          <div style={styles.stat}>
            <span style={styles.statLabel}>Open Positions</span>
            <span style={styles.statVal}>{open.length}</span>
          </div>
        </div>
      )}

      {trades.length === 0 && (
        <div style={styles.empty}>
          No trades saved yet. Open a trade card in the Scanner tab and click "SAVE TO JOURNAL".
        </div>
      )}

      {/* Open trades */}
      {open.length > 0 && (
        <>
          <div style={styles.sectionTitle}>OPEN POSITIONS</div>
          {open.map(t => (
            <TradeRow
              key={t.id}
              trade={t}
              onClose={() => { setCloseTarget(t); setExitCredit('') }}
              onDelete={() => deleteTrade(t.id)}
            />
          ))}
        </>
      )}

      {/* Closed trades */}
      {closed.length > 0 && (
        <>
          <div style={{ ...styles.sectionTitle, marginTop: '24px' }}>CLOSED TRADES</div>
          {closed.map(t => (
            <TradeRow
              key={t.id}
              trade={t}
              onDelete={() => deleteTrade(t.id)}
            />
          ))}
        </>
      )}

      {/* Close trade modal */}
      {closeTarget && (
        <div style={styles.modalOverlay} onClick={() => setCloseTarget(null)}>
          <div style={styles.modal} onClick={e => e.stopPropagation()}>
            <div style={styles.modalTitle}>CLOSE TRADE</div>
            <div style={styles.modalSymbol}>
              {closeTarget.symbol} — {closeTarget.structure}
            </div>
            <div style={{ fontSize: '12px', color: '#888', fontFamily: 'monospace', marginBottom: '12px' }}>
              Entry debit: {closeTarget.entry_debit != null ? `$${Number(closeTarget.entry_debit).toFixed(2)}` : '—'} × {closeTarget.contracts ?? '—'} contracts
            </div>
            <label style={styles.modalLabel}>
              Exit credit (per spread)
              <input
                type="number" step="0.01" min="0"
                value={exitCredit}
                onChange={e => setExitCredit(e.target.value)}
                placeholder="e.g. 4.50"
                style={styles.modalInput}
                autoFocus
              />
            </label>
            {exitCredit && !isNaN(parseFloat(exitCredit)) && (
              <div style={{
                fontFamily: 'monospace', fontSize: '13px', marginBottom: '12px',
                color: parseFloat(exitCredit) > closeTarget.entry_debit ? '#00ffaa' : '#ff4444',
              }}>
                P&L: {parseFloat(exitCredit) > closeTarget.entry_debit ? '+' : ''}
                ${Math.round((parseFloat(exitCredit) - closeTarget.entry_debit) * closeTarget.contracts * 100)}
                {' '}({Math.round(((parseFloat(exitCredit) - closeTarget.entry_debit) / closeTarget.entry_debit) * 100)}%)
              </div>
            )}
            <div style={{ display: 'flex', gap: '8px' }}>
              <button style={styles.modalBtn} onClick={() => setCloseTarget(null)}>CANCEL</button>
              <button style={{ ...styles.modalBtn, ...styles.modalBtnPrimary }} onClick={closeTrade}>
                CONFIRM CLOSE
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  )
}

function TradeRow({ trade, onClose, onDelete }) {
  const {
    symbol, structure, entry_date, entry_debit, contracts, total_cost,
    thesis, score_at_entry, status, exit_date, exit_credit, pnl_dollars, pnl_pct, notes,
  } = trade

  const held = daysHeld(entry_date)
  const isOpen = status === 'OPEN'

  return (
    <div style={styles.row}>
      <div style={styles.rowHeader}>
        <div>
          <span style={styles.rowSymbol}>{symbol}</span>
          <span style={styles.rowStructure}>{structure}</span>
        </div>
        <div style={{ display: 'flex', gap: '8px', alignItems: 'center' }}>
          {isOpen && (
            <span style={styles.openBadge}>OPEN</span>
          )}
          {!isOpen && (
            <span style={{
              ...styles.openBadge,
              color: pnl_dollars >= 0 ? '#00ffaa' : '#ff4444',
              border: `1px solid ${pnl_dollars >= 0 ? '#00ffaa' : '#ff4444'}`,
            }}>
              {pnl_dollars >= 0 ? '+' : ''}${pnl_dollars} ({pnl_pct >= 0 ? '+' : ''}{pnl_pct}%)
            </span>
          )}
          {isOpen && onClose && (
            <button style={styles.actionBtn} onClick={onClose}>CLOSE</button>
          )}
          <button style={{ ...styles.actionBtn, color: '#444', borderColor: '#2a2a2a' }} onClick={onDelete}>✕</button>
        </div>
      </div>
      <div style={styles.rowMeta}>
        <span>Entry {entry_debit != null ? `$${Number(entry_debit).toFixed(2)}` : '—'} × {contracts ?? '—'} = {total_cost != null ? `$${total_cost}` : '—'}</span>
        <span>Score at entry: {score_at_entry}</span>
        <span>Entry: {entry_date}</span>
        {isOpen && <span style={{ color: '#666' }}>Held {held}d</span>}
        {!isOpen && <span style={{ color: '#666' }}>Exit: {exit_date} @ ${exit_credit?.toFixed(2)}</span>}
      </div>
      {thesis && (
        <p style={styles.rowThesis}>{thesis}</p>
      )}
      {notes && (
        <p style={{ ...styles.rowThesis, color: '#555', fontStyle: 'italic' }}>{notes}</p>
      )}
    </div>
  )
}

const styles = {
  heading: {
    fontFamily: 'monospace',
    fontSize: '16px',
    fontWeight: 'bold',
    color: '#00ffaa',
    letterSpacing: '0.05em',
  },
  importMsg: {
    margin: '0 16px 8px',
    padding: '8px 12px',
    background: '#0d1f14',
    border: '1px solid #1f5c3a',
    borderRadius: '4px',
    color: '#00ffaa',
    fontFamily: 'monospace',
    fontSize: '11px',
    cursor: 'pointer',
  },
  importMsgError: {
    background: '#2a0d0d',
    border: '1px solid #663333',
    color: '#ff6666',
  },
  csvBtn: {
    padding: '4px 12px',
    background: 'none',
    border: '1px solid #2a2a3e',
    color: '#666',
    cursor: 'pointer',
    fontFamily: 'monospace',
    fontSize: '11px',
    borderRadius: '3px',
  },
  statsRow: {
    display: 'flex',
    gap: '24px',
    marginBottom: '24px',
    flexWrap: 'wrap',
  },
  stat: {
    display: 'flex',
    flexDirection: 'column',
    gap: '4px',
    background: '#0d0d1a',
    border: '1px solid #1a1a2e',
    borderRadius: '4px',
    padding: '12px 16px',
    minWidth: '100px',
  },
  statLabel: {
    fontFamily: 'monospace',
    fontSize: '10px',
    color: '#555',
    letterSpacing: '0.08em',
  },
  statVal: {
    fontFamily: 'monospace',
    fontSize: '20px',
    fontWeight: 'bold',
    color: '#aaa',
  },
  sectionTitle: {
    fontFamily: 'monospace',
    fontSize: '10px',
    color: '#555',
    letterSpacing: '0.1em',
    marginBottom: '8px',
  },
  empty: {
    color: '#555',
    fontFamily: 'monospace',
    fontSize: '13px',
    padding: '32px 0',
  },
  row: {
    background: '#0d0d1a',
    border: '1px solid #1a1a2e',
    borderRadius: '4px',
    padding: '12px 16px',
    marginBottom: '8px',
  },
  rowHeader: {
    display: 'flex',
    justifyContent: 'space-between',
    alignItems: 'flex-start',
    marginBottom: '6px',
    flexWrap: 'wrap',
    gap: '8px',
  },
  rowSymbol: {
    fontFamily: 'monospace',
    fontSize: '15px',
    fontWeight: 'bold',
    color: '#ddd',
    marginRight: '10px',
  },
  rowStructure: {
    fontFamily: 'monospace',
    fontSize: '12px',
    color: '#888',
  },
  openBadge: {
    fontFamily: 'monospace',
    fontSize: '11px',
    color: '#00ffaa',
    border: '1px solid #00ffaa',
    padding: '2px 8px',
    borderRadius: '3px',
  },
  actionBtn: {
    background: 'none',
    border: '1px solid #333',
    color: '#aaa',
    cursor: 'pointer',
    padding: '3px 10px',
    fontFamily: 'monospace',
    fontSize: '11px',
    borderRadius: '3px',
  },
  rowMeta: {
    display: 'flex',
    gap: '16px',
    flexWrap: 'wrap',
    fontFamily: 'monospace',
    fontSize: '11px',
    color: '#666',
    marginBottom: '6px',
  },
  rowThesis: {
    fontSize: '12px',
    color: '#777',
    lineHeight: '1.5',
    marginTop: '4px',
  },
  modalOverlay: {
    position: 'fixed',
    inset: 0,
    background: 'rgba(0,0,0,0.7)',
    display: 'flex',
    alignItems: 'center',
    justifyContent: 'center',
    zIndex: 1000,
  },
  modal: {
    background: '#0d0d1a',
    border: '1px solid #2a2a3e',
    borderRadius: '6px',
    padding: '24px',
    minWidth: '320px',
    maxWidth: '480px',
    width: '90%',
  },
  modalTitle: {
    fontFamily: 'monospace',
    fontSize: '13px',
    color: '#00ffaa',
    letterSpacing: '0.1em',
    marginBottom: '8px',
  },
  modalSymbol: {
    fontFamily: 'monospace',
    fontSize: '12px',
    color: '#aaa',
    marginBottom: '16px',
  },
  modalLabel: {
    display: 'flex',
    flexDirection: 'column',
    gap: '4px',
    fontFamily: 'monospace',
    fontSize: '11px',
    color: '#666',
    marginBottom: '12px',
  },
  modalInput: {
    background: '#080810',
    border: '1px solid #2a2a3e',
    color: '#ddd',
    padding: '6px 10px',
    borderRadius: '3px',
    fontFamily: 'monospace',
    fontSize: '13px',
    width: '100%',
  },
  modalBtn: {
    padding: '8px 16px',
    background: 'none',
    border: '1px solid #333',
    color: '#aaa',
    cursor: 'pointer',
    fontFamily: 'monospace',
    fontSize: '12px',
    borderRadius: '3px',
    flex: 1,
  },
  modalBtnPrimary: {
    border: '1px solid #00ffaa',
    color: '#00ffaa',
    background: '#0a1a0f',
  },
}
