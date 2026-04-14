// Base URL from environment variable (set at build time via Vite)
const BASE_URL = import.meta.env.VITE_BACKEND_URL || 'http://localhost:8000'

export async function fetchOpportunities(filters = {}) {
  const params = new URLSearchParams({
    min_rr: filters.minRR ?? 2.0,
    max_debit: filters.maxDebit ?? 8.0,
    min_score: filters.minScore ?? 55,
    detector: filters.detector ?? 'all',
  })
  const res = await fetch(`${BASE_URL}/opportunities?${params}`)
  if (!res.ok) throw new Error(`API error: ${res.status}`)
  return res.json()
}

export async function fetchHealth() {
  const res = await fetch(`${BASE_URL}/health`)
  if (!res.ok) throw new Error(`Health check failed: ${res.status}`)
  return res.json()
}
