# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What This Project Does

A QQQ options scanner that detects mispriced options contracts across the QQQ holdings in `qqq_holdings.py` (currently 93 symbols). It runs automated scans on weekdays (08:00, 09:45, 11:00, 15:45 ET, plus technical at 10:30 and CELT at 16:15), and serves a React dashboard showing trade setups with risk/reward profiles.

## Development Commands

**Backend (FastAPI, run from `backend/`):**
```bash
pip install -r requirements.txt
cp .env.example .env                    # Fill in SCHWAB_APP_KEY and SCHWAB_APP_SECRET
python auth_setup.py                    # One-time Schwab OAuth (opens browser)
uvicorn main:app --reload --port 8000   # Dev server with hot reload
```

**Frontend (React + Vite, run from `frontend/`):**
```bash
npm install
echo "VITE_BACKEND_URL=http://localhost:8000" > .env.local
npm run dev       # Dev server at http://localhost:5173
npm run build     # Production build → dist/
npm run preview   # Preview production build
```

## Architecture

### Backend (`backend/`)

- **`main.py`** — FastAPI app, endpoints + in-memory cache. **No in-process scheduler** — see Scheduling below.
- **`scanner.py`** — 9 mispricing detectors + spread constructors + P&L calculator (~1200 lines, core logic)
- **`schwab_client.py`** — OAuth + option chain fetching + IV calculation via schwab-py SDK
- **`models.py`** — Dataclasses: `OptionContract`, `OptionChainData`, `TradeSetup`, `MispricingSignal`, `MarketContext`
- **`catalyst.py`** — Earnings detection, IV trend analysis, human-readable narrative
- **`market_context.py`** — VIX regime, skip recommendations, token expiry warnings
- **`qqq_holdings.py`** — Hardcoded list of QQQ holdings, 93 symbols despite the `QQQ_TOP50` name (update quarterly). This count drives Schwab quota and memory.

### Frontend (`frontend/src/`)

- **`App.jsx`** — Tab UI (Scanner/My Trades), filter panel, 5-minute auto-refresh
- **`components/Dashboard.jsx`** — Opportunities grid
- **`components/OpportunityCard.jsx`** — Trade setup detail card
- **`api.js`** — Fetch wrapper using `VITE_BACKEND_URL`

### API Endpoints (all GET, rate-limited 10/min)

| Endpoint | Description |
|---|---|
| `GET /health` | Status, last scan time, token age days |
| `GET /opportunities` | Query params: `min_rr`, `max_debit`, `min_score`, `detector` |
| `GET /opportunity/{symbol}` | Best TradeSetup for one symbol |

### Data Flow

1. GitHub Actions (`.github/workflows/scan.yml`) calls `GET /scan` → `_run_scan()`
2. `fetch_option_chain()` → Schwab API → `OptionChainData`
3. `get_catalyst_context()` → earnings detection, IV trend, narrative
4. `run_all_detectors()` → 9 detectors produce `MispricingSignal`
5. `_construct_spread()` → bull call spread, calendar, or long call chosen by context
6. Results cached in `_cache` dict; scored ≥ 55 and RR ≥ 2.0 surfaced to frontend

### The 9 Detectors

Bullish: `iv_rank`, `skew`, `parity` (put-call), `term` (backwardation), `move` (straddle vs HV)
Bearish mirrors: `put_iv_rank`, `skew_inversion`, `put_parity`, `downside_move`

The `move` and `downside_move` detectors back out an implied vol from the ATM
straddle/put (Brenner-Subrahmanyam) and compare it to HV30. Do not compare an
option *price* ratio against a 1-sigma move — a straddle is ~0.8 sigma and a
single ATM option ~0.4, so that reads fair value as heavily underpriced.

### Scheduling

Scans are driven by **GitHub Actions**, not by an in-process scheduler. Render's
free tier stops the process after ~15 min idle; APScheduler could not fire while
stopped, and a cold start does not back-fill missed jobs — so unattended scans
silently never ran. `.github/workflows/scan.yml` now calls the trigger endpoints
on a cron, and the inbound request is what wakes the instance.

| ET slot | Endpoint | Work |
|---|---|---|
| 08:00 | `/scan` | Full scan |
| 09:45 | `/scan-sectors`, `/scan` | Sector refresh, then full scan |
| 10:30 | `/scan-setups` | Technical setups |
| 11:00 | `/scan` | Full scan |
| 15:45 | `/scan` | Full scan |
| 16:15 | `/scan-celt` | CELT scan |

GitHub cron is UTC-only, so each slot has two cron entries (EDT and EST). The
workflow keys off `github.event.schedule` to look up the intended ET time and
skips the out-of-season twin. Tolerates up to 30 min of cron lag.

Manual run: repo → Actions → Scheduled Scans → Run workflow.

## Environment Variables

**Backend `.env`:**
```
SCHWAB_APP_KEY=
SCHWAB_APP_SECRET=
SCHWAB_CALLBACK_URL=https://127.0.0.1:8182
SCHWAB_TOKEN_PATH=./token.json
ALLOWED_ORIGIN=http://localhost:5173
SUPABASE_URL=          # required in prod — see below
SUPABASE_KEY=
```

`SUPABASE_URL`/`SUPABASE_KEY` are optional locally but **required in
production**. `supabase_client._get_client()` returns `None` when they are
unset and every save/load silently no-ops, so nothing persists and each cold
start serves an empty cache. `last_scan: null` on `/health` right after a
restart is the symptom.

**Frontend `.env.local`:**
```
VITE_BACKEND_URL=http://localhost:8000
```

## Deployment

- **Backend**: Render.com (free tier, spins down after 15 min inactivity)
  - Build: `pip install -r backend/requirements.txt`
  - Start: `cd backend && uvicorn main:app --host 0.0.0.0 --port $PORT` (flat imports, no `backend/__init__.py` — `uvicorn backend.main:app` fails with ModuleNotFoundError)
  - `token.json` uploaded as a secret file to `/etc/secrets/token.json`
- **Frontend**: GitHub Pages (`https://<user>.github.io/mispriced-scanner/`)
  - GitHub Actions auto-deploys on push to `main` when `frontend/**` changes
  - `BACKEND_URL` GitHub secret injected at build time

## Key Operational Notes

- **Token expiry**: Schwab OAuth token expires every 7 days. Run `python backend/auth_setup.py` locally, then re-upload `token.json` to Render as a secret file.
- **IV rank history**: Stored in-memory per symbol; resets on server restart.
- **Schwab rate limits**: 120 req/min. `fetch_option_chain` makes **two** calls per symbol (chain + price history), so a 93-symbol scan is ~186 requests. `fetch_all_chains` paces batches of 5 to hold ~90 req/min, making a full scan take ~2-3 minutes.
- **CORS**: Configured for specific origins only (`localhost:3000`, `localhost:5173`, `ALLOWED_ORIGIN`).