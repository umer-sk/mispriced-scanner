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

### Forward Test

Every setup the scanner surfaces (`TradeSetup` from the main scan, `TechnicalSetup`
from the technical scan) is recorded and marked to market on the existing scan
ticks until it resolves against fixed exit rules: **+50%** of entry debit (first
target, half off), **+100%** (second target, remainder off), **−50%** (stop, whole
position out) — all measured on spread mid. It answers "do these setups make
money, and which detectors are worth trusting" — something the existing
`TradeJournal` (localStorage, `qqq_journal`) cannot, because it only records
trades the user chose to save, so its sample is filtered by the user's own
judgement.

- **`backend/forward_test.py`** — normalises `TradeSetup`/`TechnicalSetup` onto
  one shape, classifies each into a tier, runs the outcome state machine, and
  aggregates closed positions into stats.
- **`backend/ft_store.py`** — Supabase persistence for two tables, `ft_positions`
  (one row per tracked setup) and `ft_marks` (one row per re-price). DDL lives in
  `docs/sql/forward_test.sql` and **must be applied by hand in the Supabase SQL
  editor** — it has not been applied yet. Until it is, every `ft_store` function
  hits a missing table, catches the error, and no-ops silently: nothing is
  recorded and nothing complains.
- **`backend/occ.py`** — builds OCC option symbols so `fetch_quotes()` can
  re-price a specific known contract by symbol instead of re-fetching a chain
  (chains are ATM-centred and a position that has moved deep ITM/OTM can fall
  out of one).

**Entry points.** `snapshot_setups(setups, source)` and `mark_open_positions()`
are both called from `_run_scan` (`main.py`), after the `chains_ok` guard so a
failed scan never records positions, and `mark_open_positions()` alone runs
after `snapshot_setups(setups, "technical")` inside `_run_technical_scan`. Both
functions do sequential Supabase/Schwab I/O, so both run via
`run_in_executor` — off the event loop, like every other blocking call in these
scan functions — so `/health` and `/opportunities` don't stall and the scan
lock doesn't hold the loop hostage for other triggers while `_scan_lock` is
held.

**Tiers.** Each setup is classified A/B/C at log time from seven gates
(quality, RR, liquidity, no earnings in window, 25–45 DTE, required move,
trend), but filtering happens at *read* time — everything that clears the
scanner's own surfacing filter is recorded, tier and all, so near-misses are
available to check whether the gates are set correctly. Two gates carry the
real discriminating power and are the only ones with a defined Tier B
near-miss band:
- **quality** (`score >= 60` / `signal_count >= 5`) — a quality screen.
- **breakeven move** (`abs(breakeven_move_pct) <= 3.5`) — the actual probability
  content; a stock that must move 3.5% just to reach breakeven needs
  materially more to reach +50%.

Every other gate is pass/fail with no "nearly" — failing one of those alone is
still Tier C.

**Read the caveats below with every number this feature produces:**

1. **Mid, not fills.** Targets and stops are evaluated on the spread mid; real
   fills cross the bid/ask. Live trading will underperform these figures, more
   so on wider spreads.
2. **Thin sample.** Roughly 1–3 new positions a day. Any per-detector (or
   per-tier, per-source) row is only meaningful read alongside its `n` —
   `aggregate()` and the UI both surface `n` for exactly this reason.
3. **`unpriceable` is not a loss.** A position that fails to get a quote 8
   marks running is closed as `unpriceable` and excluded from statistics
   entirely — it counts as neither a win nor a loss.

**Trap for a future maintainer:** `fetch_quotes()` in `schwab_client.py` keys
its returned dict by whatever symbol string Schwab echoes back in the response
payload, while `mark_open_positions()` looks up quotes by the symbol it
requested (`long_occ`/`short_occ`). These are assumed identical. If Schwab ever
normalizes or reformats the symbol on the way back, every lookup misses,
`mark_failures` increments on every open position on every tick, and after 8
ticks the entire dataset quietly closes itself out as `unpriceable` — with no
exception, no log spike, nothing to point at. `backend/tests/test_fetch_quotes.py`
(`test_request_response_key_identity`) pins the current identity behavior;
if that test ever needs to change to accommodate a Schwab response format
change, `mark_open_positions` needs a corresponding fix, not just the test.

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
workflow keys off `github.event.schedule` to look up which season that cron
belongs to, compares it against the current ET UTC offset, and skips the
out-of-season twin.

Do not reintroduce a drift/tolerance check here. The first version inferred the
season from how close the run landed to its intended time, with a 30-minute
tolerance. GitHub delivered these crons **1.5-3.5 hours late** in practice, so
every run was judged off-season and skipped — 13 runs reported "success" and
triggered zero scans. Widening the tolerance is not an option either: the twins
are exactly 60 minutes apart. Scheduled runs may also be dropped entirely under
load, so treat the schedule as best-effort and watch `last_scan` on /health.

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