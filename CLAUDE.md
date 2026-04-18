# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What This Project Does

A QQQ options scanner that detects mispriced options contracts across the 30 largest QQQ holdings. It runs automated scans at 8AM, 9:45AM, and 11AM ET on weekdays, and serves a React dashboard showing trade setups with risk/reward profiles.

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

- **`main.py`** — FastAPI app with 3 endpoints + APScheduler (3 daily scans)
- **`scanner.py`** — 5 mispricing detectors + spread constructor + P&L calculator (~600 lines, core logic)
- **`schwab_client.py`** — OAuth + option chain fetching + IV calculation via schwab-py SDK
- **`models.py`** — Dataclasses: `OptionContract`, `OptionChainData`, `TradeSetup`, `MispricingSignal`, `MarketContext`
- **`catalyst.py`** — Earnings detection, IV trend analysis, human-readable narrative
- **`market_context.py`** — VIX regime, skip recommendations, token expiry warnings
- **`qqq_holdings.py`** — Hardcoded list of 30 QQQ holdings (update quarterly)

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

1. APScheduler triggers `scan_all()` at scheduled times
2. `fetch_option_chain()` → Schwab API → `OptionChainData`
3. `get_catalyst_context()` → earnings detection, IV trend, narrative
4. `run_all_detectors()` → 5 detectors produce `MispricingSignal`
5. `_construct_spread()` → bull call spread, calendar, or long call chosen by context
6. Results cached in `_cache` dict; scored ≥ 55 and RR ≥ 2.0 surfaced to frontend

### The 5 Detectors

`iv_rank`, `skew`, `parity` (put-call), `term` (backwardation), `move` (straddle vs HV)

## Environment Variables

**Backend `.env`:**
```
SCHWAB_APP_KEY=
SCHWAB_APP_SECRET=
SCHWAB_CALLBACK_URL=https://127.0.0.1
SCHWAB_TOKEN_PATH=./token.json
ALLOWED_ORIGIN=http://localhost:5173
```

**Frontend `.env.local`:**
```
VITE_BACKEND_URL=http://localhost:8000
```

## Deployment

- **Backend**: Render.com (free tier, spins down after 15 min inactivity)
  - Build: `pip install -r backend/requirements.txt`
  - Start: `uvicorn backend.main:app --host 0.0.0.0 --port $PORT`
  - `token.json` uploaded as a secret file to `/etc/secrets/token.json`
- **Frontend**: GitHub Pages (`https://<user>.github.io/mispriced-scanner/`)
  - GitHub Actions auto-deploys on push to `main` when `frontend/**` changes
  - `BACKEND_URL` GitHub secret injected at build time

## Key Operational Notes

- **Token expiry**: Schwab OAuth token expires every 7 days. Run `python backend/auth_setup.py` locally, then re-upload `token.json` to Render as a secret file.
- **IV rank history**: Stored in-memory per symbol; resets on server restart.
- **Schwab rate limits**: 120 req/min — scanner batches 10 symbols with 2-second delays between batches.
- **CORS**: Configured for specific origins only (`localhost:3000`, `localhost:5173`, `ALLOWED_ORIGIN`).