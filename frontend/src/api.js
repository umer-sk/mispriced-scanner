// Base URL from environment variable (set at build time via Vite)
const BASE_URL = import.meta.env.VITE_BACKEND_URL || 'http://localhost:8000'

export async function fetchOpportunities(filters = {}) {
  // Only surfaces Tier A (backend main._is_tier_a) — no min_rr/min_score
  // slider; detector and direction are real preferences, not a quality bar.
  const params = new URLSearchParams({
    detector:  filters.detector   ?? 'all',
    direction: filters.direction  ?? 'both',
  })
  const res = await fetch(`${BASE_URL}/opportunities?${params}`)
  if (!res.ok) throw new Error(`API error: ${res.status}`)
  return res.json()
}

export async function fetchSectorAnalysis() {
  const res = await fetch(`${BASE_URL}/sector-analysis`)
  if (!res.ok) throw new Error(`Sector analysis error: ${res.status}`)
  return res.json()
}

export async function triggerScan() {
  const res = await fetch(`${BASE_URL}/scan`)
  if (!res.ok) throw new Error(`Scan failed: ${res.status}`)
  return res.json()
}

export async function fetchHealth() {
  const res = await fetch(`${BASE_URL}/health`)
  if (!res.ok) throw new Error(`Health check failed: ${res.status}`)
  return res.json()
}

export async function fetchTechnicalSetups(filters = {}) {
  // Only surfaces Tier A (backend main._is_tier_a) — no min_rr slider; the
  // 200W bounce gets its own lower R:R floor there via forward_test's
  // per-source override, not a query param here.
  const params = new URLSearchParams({
    direction: filters.direction ?? 'both',
    sort:      filters.sort      ?? 'rr',
  })
  const res = await fetch(`${BASE_URL}/technical-setups?${params}`)
  if (!res.ok) throw new Error(`Technical setups error: ${res.status}`)
  return res.json()
}

export async function triggerSetupsScan() {
  const res = await fetch(`${BASE_URL}/scan-setups`)
  if (!res.ok) throw new Error(`Setups scan failed: ${res.status}`)
  return res.json()
}

export async function fetchCeltSetups(filters = {}) {
  // Only surfaces Tier A (backend main._is_tier_a) — no min-score slider.
  const params = new URLSearchParams({
    sort: filters.sort ?? 'score',
  })
  const res = await fetch(`${BASE_URL}/celt-setups?${params}`)
  if (!res.ok) throw new Error(`CELT setups error: ${res.status}`)
  return res.json()
}

export async function triggerCeltScan() {
  const res = await fetch(`${BASE_URL}/scan-celt`)
  if (!res.ok) throw new Error(`CELT scan failed: ${res.status}`)
  return res.json()
}

export async function fetchForwardTest() {
  const res = await fetch(`${BASE_URL}/forward-test`)
  if (!res.ok) throw new Error(`Forward test error: ${res.status}`)
  return res.json()
}

export async function fetchForwardTestPositions(params = {}) {
  const q = new URLSearchParams(params).toString()
  const res = await fetch(`${BASE_URL}/forward-test/positions${q ? `?${q}` : ''}`)
  if (!res.ok) throw new Error(`Forward test positions error: ${res.status}`)
  return res.json()
}

// occSymbols: array of OCC option symbols (TradeJournal.jsx entries carry
// these as long_occ/short_occ). Returns { quotes: { occSymbol: mid } } —
// a symbol Schwab couldn't quote is simply absent, not an error.
export async function fetchPositionQuotes(occSymbols) {
  if (!occSymbols.length) return { quotes: {} }
  const params = new URLSearchParams()
  for (const occ of occSymbols) params.append('occ', occ)
  const res = await fetch(`${BASE_URL}/position-quotes?${params}`)
  if (!res.ok) throw new Error(`Position quotes error: ${res.status}`)
  return res.json()
}
