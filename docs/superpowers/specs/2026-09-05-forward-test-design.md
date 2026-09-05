# Forward Test — Design

**Date:** 2026-09-05
**Status:** Approved, not yet implemented

## The question this answers

*Do the setups this scanner surfaces actually make money, and which detectors are
worth trusting?*

Today there is no way to know. Setups appear on the dashboard, disappear on the
next scan, and nothing records what happened next. The existing `TradeJournal`
(localStorage, `qqq_journal`) records only trades the user chose to save, which
answers a different question — *am I executing well* — and is useless for
evaluating the detectors, because the sample is filtered by the user's own
judgement. A detector could have a 40% win rate and look excellent in the
journal simply because the user skipped its bad signals.

This design adds an automatic, unfiltered record: every surfaced setup is
snapshotted at first sighting and marked to market on the existing scan ticks
until it resolves.

## Scope

**In:** `TradeSetup` (the main scanner) and `TechnicalSetup` (the technical
scanner). Both are swing-horizon and therefore comparable to each other.

**Out:** `CeltSetup`. Its LEAP horizon is months to years, so it would produce no
readable statistics for a long time, and its outcome rules would differ.

**Out:** any change to `TradeJournal`. It stays exactly as it is, recording real
trades. The two datasets are joined later, if ever — the interesting comparison
is whether discretion beats the raw signal, and that requires both to exist
independently.

## Exit rules

Fixed by the user, measured on the spread mid as a percentage of entry debit
(`net_debit` for `TradeSetup`, `premium` for `TechnicalSetup`):

| Level | Meaning |
|---|---|
| **+50%** | first profit take — half the position off |
| **+100%** | second profit take — remainder off |
| **−50%** | stop — whole position out |

## What gets logged

Everything that clears the existing scan filter (`score >= 45`,
`rr_ratio >= 2.0`, `liquidity_ok`) plus every `TechnicalSetup` that clears its
own filter — each tagged with a tier computed at log time.

Filtering happens at **read** time, never at write time. The reason is
empirical: a scan on 2026-09-05 produced 6 raw setups from 93 symbols, of which
3 passed. Applying the Tier A gates on top of that plausibly yields zero on most
days, and a tracker that logs nothing teaches nothing. Recording near-misses is
also the only way to learn whether the thresholds are in the right place — if
Tier A wins 55% and Tier B wins 58%, the gates are discarding good trades, and
that is invisible if only Tier A is stored.

### Tier definition

**Tier A** — all applicable gates pass:

| # | Gate | `TradeSetup` | `TechnicalSetup` |
|---|---|---|---|
| 1 | Quality | `score >= 60` | `signal_count >= 5` (of 7) |
| 2 | Reward | `rr_ratio >= 2.0` | same |
| 3 | Liquidity | `liquidity_ok` and both `*_spread_pct <= 6` | **see prerequisite** |
| 4 | No binary event | `not catalyst.earnings_in_window` | `not earnings_within_dte` |
| 5 | Horizon | `25 <= dte <= 45` | same |
| 6 | Required move | `abs(breakeven_move_pct) <= 3.5` | same |
| 7 | Trend | `technical_context.bias` not opposing the structure; missing context counts as neutral | `direction` is itself the trend signal — always passes |

**Tier B** — fails exactly one gate, *and* that gate is one of the two with a
defined near-miss band:

- quality within 10 points (`score` 50–59) or 1 signal (`signal_count` 4) of threshold, or
- `breakeven_move_pct` in (3.5, 5.0]

Gates 2, 3, 4, 5 and 7 are pass/fail with no meaningful "nearly" — a setup
failing one of those is Tier C even if it is its only failure.

**Tier C** — everything else.

Note what this means per source. For `TechnicalSetup`, gate 1 is satisfied by
construction (`NET_SCORE_THRESHOLD = 3` already requires 5 of 7 signals to
agree) and gate 7 is tautological (`direction` *is* the trend read), so only
gates 2–6 discriminate. **Tier A therefore does not mean the same thing across
the two sources**, and tier comparisons should be made within a source, not
across. Once the prerequisite below lands, gate 3 becomes real for technical
setups and the two get closer, but not identical.

`gates_failed` stores the list of failing gate names on every row, so thresholds
can be recalibrated from evidence rather than re-guessed.

#### Rationale for the two load-bearing gates

**Gate 6 (required move)** is the actual probability content. If the stock must
move 3.5% just to reach breakeven, reaching +50% of debit needs materially more.
Everything else is a quality screen.

**Gate 3 (liquidity)** matters more than it looks *given these specific exit
rules*. Targets and stops are evaluated on mid, but fills cross the bid/ask, and
a two-leg spread crosses roughly four half-spreads round trip. At the current
`liquidity_ok` bound of 10% per leg, friction consumes about a fifth of the +50%
target. Tightening to 6% keeps "+50% on mid" close to +44% realised.

Deliberately **not** used: `probability_of_profit`. It is
`abs(long_leg.delta) * 100` ([scanner.py:778](../../../backend/scanner.py)) —
roughly P(stock above the *long strike*), not P(above breakeven). It is
systematically optimistic, by more the wider the debit, so filtering on it would
select precisely the setups where the optimism is largest.

## Prerequisite

`TechnicalSetup` carries no open-interest or bid/ask-spread fields, and
`technical_scanner.py` does not apply the OI ≥ 100 / spread ≤ 15% hard gates
that `scanner.py` enforces. Gate 3 therefore cannot be evaluated for technical
setups, and Tier A would not be comparable across the two sources.

**Before implementing this design:** add `long_leg_oi`, `short_leg_oi`,
`long_leg_spread_pct`, `short_leg_spread_pct` and `liquidity_ok` to
`TechnicalSetup`, populated in `_find_delta_contract` and the two spread
constructors, and apply the same hard gates. This is worth doing on its own
merits — a technical setup can currently be built on a contract with OI 0 and a
40% bid/ask.

## Data model

`scanner_cache` cannot hold this: one row per `cache_key`, last-write-wins, no
history. Two new tables.

```sql
create table ft_positions (
  id                  uuid primary key default gen_random_uuid(),
  dedup_key           text not null,
  source              text not null,           -- 'scanner' | 'technical'

  symbol              text not null,
  detector            text,                    -- null for technical setups
  structure           text not null,
  direction           text not null,           -- 'bullish' | 'bearish'

  expiry              date not null,
  dte_at_entry        int  not null,
  long_strike         numeric not null,
  short_strike        numeric,
  long_occ            text,
  short_occ           text,

  entry_ts            timestamptz not null,
  entry_debit         numeric not null,
  entry_stock_price   numeric not null,
  score_at_entry      int,
  rr_at_entry         numeric,
  breakeven_move_pct  numeric,

  tier                text not null,           -- 'A' | 'B' | 'C'
  gates_failed        jsonb not null default '[]',

  status              text not null default 'open',
    -- 'open' | 'target1' | 'target2' | 'stopped' | 'expired' | 'unpriceable'
  t1_ts               timestamptz,
  t2_ts               timestamptz,
  stop_ts             timestamptz,
  closed_ts           timestamptz,

  mfe_pct             numeric,                 -- max favourable excursion
  mae_pct             numeric,                 -- max adverse excursion
  realized_pnl_pct    numeric,

  times_seen          int not null default 1,
  last_seen_ts        timestamptz not null,
  mark_failures       int not null default 0
);

-- Dedup is against OPEN positions only, so the same setup recurring after a
-- resolution correctly opens a new row.
create unique index ft_positions_open_dedup
  on ft_positions (dedup_key)
  where closed_ts is null;

create index ft_positions_open on ft_positions (status) where closed_ts is null;

create table ft_marks (
  position_id  uuid not null references ft_positions(id) on delete cascade,
  ts           timestamptz not null,
  spread_mid   numeric not null,
  pnl_pct      numeric not null,
  stock_price  numeric,
  primary key (position_id, ts)
);
```

`dedup_key` is `{symbol}|{detector}|{structure}|{expiry}|{long_strike}|{short_strike}`.
Strikes belong in the key: if the same symbol and detector produce a different
strike pair on a later scan because the stock moved, that is a different trade
with a different risk profile, not a duplicate.

Volume: at ~3 setups per scan with dedup, expect 1–3 new positions per day.
`ft_marks` at 50 open positions × 4 ticks/day is ~12k rows over two months.

## OCC symbols

Re-pricing needs to quote specific known contracts. `client.get_quotes(symbols)`
exists in the installed schwab-py and accepts OCC option symbols, which sidesteps
the `strike_count=20` ATM window entirely — a setup that moves deep ITM or OTM
would otherwise fall out of a later chain, losing exactly the trades that moved
most.

Two sources, in order:

1. **Capture.** `_parse_contracts` ([schwab_client.py:114](../../../backend/schwab_client.py))
   currently discards the `symbol` field from the raw Schwab payload. Add
   `occ_symbol` to `OptionContract` and carry it into `TradeSetup` /
   `TechnicalSetup` as `long_occ` / `short_occ`.
2. **Reconstruct**, as fallback and for rows created before (1) lands: 6-char
   root left-justified space-padded, `YYMMDD`, `C`/`P`, then strike × 1000
   zero-padded to 8 digits. E.g. `NVDA  261016C00190000`.

## Components

New module `backend/forward_test.py`. Two entry points, both called from the
existing scan functions — no new scheduler, no new cron. Everything runs on the
four existing ticks (08:00, 09:45, 11:00, 15:45 ET), which is also why marks are
four-a-day rather than daily: between two daily closes a spread can reach +50%
and fall back through the stop, and a daily mark would record only the loss,
biasing every result pessimistically.

### `snapshot_setups(setups, source) -> int`

Called from `_run_scan` (after the `chains_ok` guard, so a failed scan never
logs) and `_run_technical_scan`. For each setup:

- normalise to the common shape (see below)
- compute `tier` and `gates_failed`
- if `dedup_key` matches an **open** row: `times_seen += 1`, `last_seen_ts =
  now`, **entry price and entry timestamp are never touched**
- else insert a new position

`times_seen` is worth keeping rather than discarding: a setup that persists
across all four daily scans is a different animal from one that flickers in and
out on a single mid quote, and whether persistence predicts anything is itself a
question this dataset can answer.

### `mark_open_positions() -> int`

Called on the same ticks. Loads open positions, batches every `long_occ` /
`short_occ` into `client.get_quotes()`, then per position:

```
spread_mid = long_mid - short_mid          # debit vertical; single leg: long_mid
pnl_pct    = (spread_mid - entry_debit) / entry_debit * 100
```

Writes an `ft_marks` row, updates `mfe_pct` / `mae_pct`, then applies the state
machine below.

Quote cost is negligible — 50 open positions is ~100 contracts, one or two
batched requests against a 120/min budget. It runs inside the existing
`_scan_lock`.

### Normalisation

The two setup types share no base class and disagree on field names. A single
`_normalise(setup, source) -> dict` handles the mapping:

| Common | `TradeSetup` | `TechnicalSetup` |
|---|---|---|
| `long_strike` | `long_strike` | `strike` |
| `entry_debit` | `net_debit` | `premium` |
| `quality` | `score` | `signal_count` |
| `detector` | `signal.detector` | `null` |
| `direction` | derived from `structure` | `direction` |
| `earnings_in_window` | `catalyst.earnings_in_window` | `earnings_within_dte` |

## Outcome state machine

Evaluated on every mark, in this order:

| Condition | Effect |
|---|---|
| `pnl_pct >= 100` | set `t2_ts`, `status='target2'`, close |
| `pnl_pct >= 50` and `t1_ts` is null | set `t1_ts`, `status='target1'`, **stays open** |
| `pnl_pct <= -50` | set `stop_ts`, `status='stopped'`, close |
| `today > expiry` | `status='expired'`, close using the last mark |
| `mark_failures >= 8` | `status='unpriceable'`, close, excluded from stats |

A position that has hit T1 remains open, because the second half is still
running and can still reach T2 or be stopped.

### Realised P&L

Blended scale-out, half the position at each target:

| Path | Realised |
|---|---|
| T1 → T2 | `(50 + 100) / 2` = **+75%** |
| T1 → stopped | `(50 + −50) / 2` = **0%** |
| T1 → expired at *X* | `(50 + X) / 2` |
| stopped, no T1 | **−50%** |
| expired at *X*, no T1 | *X* |

## API

| Endpoint | Returns |
|---|---|
| `GET /forward-test` | aggregates: count, win rate, avg realised P&L, avg MFE/MAE, avg hold days — grouped by tier, by detector, by source |
| `GET /forward-test/positions` | rows, filterable by `status`, `tier`, `detector`, `source` |

Aggregates are computed over **closed** positions only; open ones are reported
separately as a count so the numbers aren't quietly diluted.

## Frontend

A `FORWARD TEST` tab following the existing pattern in
[App.jsx](../../../frontend/src/App.jsx) — import the component, add a button to
the tab bar, add a conditional render. Two sections: a summary table (win rate
and avg realised P&L by tier and by detector) and a positions table showing
status, entry, current mark, MFE/MAE and milestone timestamps.

## Failure modes

- **Supabase unavailable.** Log and continue. The forward test must never block
  or fail a scan.
- **Quote missing for a contract.** Skip that mark, increment `mark_failures`,
  leave the position open. Do not treat a missing quote as a price move.
- **Expiry passes with no final mark.** Close as `expired` using the last
  recorded mark, not as `unpriceable`.
- **Position never marked at all** (e.g. bad OCC symbol). After 8 consecutive
  failures, close as `unpriceable` and exclude from statistics rather than
  silently reporting it as a loss.

## Known limitations — state these wherever results are displayed

**This measures the signal, not achievable P&L.** Targets and stops are evaluated
on mid; real fills cross the spread. Expect live trading to underperform these
numbers, more so on wider spreads.

**Four marks a day is not continuous.** An intraday spike through +50% and back
can be missed. Results are therefore mildly conservative on the upside.

**Sample size will be thin.** ~1–3 new positions per day is ~60–180 over three
months — adequate in aggregate and for comparing Tier A against Tier B, but
probably *not* enough for confident per-detector win rates. Per-detector
breakdowns should show sample counts next to every number.

**Nothing before implementation is recoverable.** There is no historical option
price data available here, so the record starts empty and only accumulates
forward.

## Explicitly out of scope

- Backfilling history.
- Modelling commissions or slippage.
- Any automated trading action. This is measurement only.
- CELT setups.
