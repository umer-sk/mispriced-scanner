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
