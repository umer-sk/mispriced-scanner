// Base URL from environment variable (set at build time via Vite)
const BASE_URL = import.meta.env.VITE_BACKEND_URL || 'http://localhost:8000'

export async function fetchOpportunities(filters = {}) {
  const params = new URLSearchParams({
    min_rr:    filters.minRR      ?? 2.0,
    max_debit: filters.maxDebit   ?? 8.0,
    min_score: filters.minScore   ?? 55,
    detector:  filters.detector   ?? 'all',
    direction: filters.direction  ?? 'both',
  })
  if (filters.minOI) params.set('min_oi', '100')
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

export async function fetchSectorAnalysis() {
  const res = await fetch(`${BASE_URL}/sector-analysis`)
  if (!res.ok) throw new Error(`Sector analysis failed: ${res.status}`)
  return res.json()
}

export async function fetchTechnicalSetups(filters = {}) {
  const params = new URLSearchParams({
    direction: filters.direction ?? 'both',
    min_rr:    filters.minRR     ?? 2.0,
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
