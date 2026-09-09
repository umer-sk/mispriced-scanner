// Shared trade-journal storage. Three tabs (Scanner, Technical Setups, Crash
// Leaps) can each save a position, and TradeJournal.jsx reads what they
// wrote — a single source of truth for the storage key and the append
// operation keeps that in sync. The journal's own read/replace operations
// (close, delete, import) stay in TradeJournal.jsx, since only that tab
// performs them.
export const JOURNAL_STORAGE_KEY = 'qqq_journal'

export function readJournal() {
  try {
    return JSON.parse(localStorage.getItem(JOURNAL_STORAGE_KEY) || '[]')
  } catch {
    return []
  }
}

/** Build one journal entry in the shape TradeJournal.jsx expects.
 *
 * long_occ/short_occ are OCC symbols (schwab_client.fetch_quotes can price
 * them later) — short_occ is '' for a single-leg position (long call/put,
 * or a CELT LEAP). Absent/undeterminable is '', never a guessed value.
 */
export function buildJournalEntry({
  symbol, structureLabel, entryDebit, contracts, thesis, scoreAtEntry, notes,
  longOcc, shortOcc,
}) {
  return {
    id: crypto.randomUUID(),
    symbol,
    structure: structureLabel,
    entry_date: new Date().toISOString().split('T')[0],
    entry_debit: entryDebit,
    contracts,
    total_cost: Math.round(entryDebit * contracts * 100),
    thesis: thesis ?? null,
    score_at_entry: scoreAtEntry ?? null,
    status: 'OPEN',
    exit_date: null,
    exit_credit: null,
    pnl_dollars: null,
    pnl_pct: null,
    notes: notes || '',
    long_occ: longOcc || '',
    short_occ: shortOcc || '',
  }
}

/** Append one new trade to the journal. save() writes first, throws on
 * quota/private-browsing failure before any caller assumes it succeeded. */
export function saveNewTrade(fields) {
  const journal = readJournal()
  journal.push(buildJournalEntry(fields))
  localStorage.setItem(JOURNAL_STORAGE_KEY, JSON.stringify(journal))
}
