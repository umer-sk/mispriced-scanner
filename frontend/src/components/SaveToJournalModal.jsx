// Shared "save this position to the journal" dialog — used by Dashboard.jsx,
// TechnicalSetups.jsx, and CeltSetups.jsx. Keeping one copy means the three
// save flows can't drift into three slightly different modals.
export default function SaveToJournalModal({
  symbolLine, unitCost, contracts, onContractsChange, notes, onNotesChange,
  onCancel, onConfirm,
}) {
  return (
    <div style={styles.modalOverlay} onClick={onCancel}>
      <div style={styles.modal} onClick={e => e.stopPropagation()}>
        <div style={styles.modalTitle}>SAVE TO JOURNAL</div>
        <div style={styles.modalSymbol}>{symbolLine}</div>
        <label style={styles.modalLabel}>
          Contracts
          <input
            type="number" min="1" max="100" value={contracts}
            onChange={e => onContractsChange(parseInt(e.target.value) || 1)}
            style={styles.modalInput}
          />
        </label>
        <div style={{ fontSize: '12px', color: '#888', fontFamily: 'monospace', marginBottom: '12px' }}>
          Total cost: ${(unitCost * contracts * 100).toFixed(0)}
        </div>
        <label style={styles.modalLabel}>
          Notes (optional)
          <textarea
            value={notes}
            onChange={e => onNotesChange(e.target.value)}
            placeholder="Why you're taking this trade..."
            style={{ ...styles.modalInput, height: '80px', resize: 'vertical' }}
          />
        </label>
        <div style={{ display: 'flex', gap: '8px', marginTop: '12px' }}>
          <button style={styles.modalBtn} onClick={onCancel}>CANCEL</button>
          <button style={{ ...styles.modalBtn, ...styles.modalBtnPrimary }} onClick={onConfirm}>
            SAVE
          </button>
        </div>
      </div>
    </div>
  )
}

const styles = {
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
