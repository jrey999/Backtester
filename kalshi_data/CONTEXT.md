# Kalshi CFB market research — context for a new session

This file exists so a fresh Claude session (or a human) can pick up this
project without re-deriving everything below. It lives in `kalshi_data/`
inside the `Backtester` repo, on branch `claude/siu-samford-kalshi-data-59r9tn`.

## What this project is

Pulling and analyzing Kalshi prediction-market data for college football
games, originally to research an upset scenario (Southern Illinois @
Samford, Sept 3, 2026), then generalized into a reusable tool, then a
persistent dataset covering the whole two-week CFB slate. Next phase
(not started yet): modeling signals from the accumulated data.

## Key API facts (learned the hard way)

- **Base URL**: `https://api.elections.kalshi.com/trade-api/v2`. The old
  host `https://trading-api.kalshi.com` is decommissioned — it now
  returns HTTP 401 with a "moved" notice. If this breaks again, check
  https://trading-api.readme.io/reference.
- **No API key needed** for anything used here — events, markets, trades,
  candlesticks, orderbook are all public read endpoints.
- **Series**: `KXNCAAFGAME` ("College Football Game") covers every game
  regardless of division — FBS, FCS, D-III all show up in it. Each event
  (one game) has exactly 2 outcome markets, ticker suffixed `-<TEAM>`
  (e.g. `KXNCAAFGAME-26SEP03SIUSAM-SIU` / `...-SAM`).
- **Event ticker date format**: `KXNCAAFGAME-YYMONDDXXXYYY` (e.g.
  `26SEP03SIUSAM` = Sept 3, 2026, away team SIU, home team SAM — but
  don't over-trust the away/home ordering, just treat both as "the two
  outcomes"). `bulk_pull.py` parses this pattern to filter by date.
- **`/events` pagination**: must page through with `cursor` until it
  comes back empty — a single page can silently miss the game you want
  even when it exists (this cost real time early in the session; the
  event you're looking for might just be on a later page).
- **`/markets?series_ticker=...`** (not `/events`) is the fast way to
  scan *all* markets' `last_price_dollars` / `previous_price_dollars` /
  `volume_fp` at once without pulling full trade history per game — used
  to pick "interesting movement" games without an expensive full scan.

## The core methodological finding: thin markets lie about price

This dataset is **very illiquid** for most games — most FCS/small-school
markets trade in $1–3 lots. The naive "last traded price" you'd read off
a chart is frequently **not a real consensus**, it's just whichever tiny
odd-lot last happened to cross a wide, stale, mostly-untouched bid/ask
spread. Concretely, in the SIU–Samford market on Aug 25–26, the printed
price swung 90¢ → 65¢ → 90¢ while the actual quoted book barely moved
(stayed ~66/90 the entire time, 23 straight hours with zero volume) — two
$1–3 trades did that, not new information. A similar, even starker
version happened in the Illinois State/Western Illinois market: an ask
of 25–35¢ sat completely untraded for over a week (pure default/stale
quote), while the only *real* trades that whole time (10+ contracts)
priced Western Illinois at 7–10% the entire way — the market had quietly
known this was a landslide for two weeks; a big real sweep on Aug 31
night just confirmed and deepened it.

**The fix, implemented in `liquidity_filter.py` / `kalshi_db.py`-adjacent
logic**: two independent checks, not ANDed —
- **`is_liquid`** = hourly volume ≥ 10 contracts → trust the traded price
  for that hour, *regardless of the surrounding spread* (a large trade
  crossing a wide spread is still real; don't discard it for that — this
  was a bug I had and fixed: originally ANDed volume with a tight-spread
  requirement, which wrongly threw out a legitimate 447-contract trade).
- **`tight_quote`** = spread ≤ 10¢ → only relevant when nothing traded;
  decides whether the quoted bid/ask midpoint is a decent stand-in.
- **`filtered_price`** = the traded price on liquid hours; otherwise
  carried forward from the last liquid trade; before any liquid trade
  has ever happened, falls back to the quoted mid (flagged low-confidence
  if the quote itself is wide).

Also worth knowing: a cluster of "biggest trades" is often **one large
order sweeping the book**, not many independent traders — e.g. 8 of the
10 biggest trades in the SIU–Samford dataset landed in the same 3-minute
window on Aug 31 night. `kalshi_game_report.py`'s top-10 chart
auto-detects this clustering and calls it out.

## Files in `kalshi_data/`

**Original one-off pull** (SIU–Samford specific, superseded by the
generalized tool below but left in place — documents the original
analysis):
- `build_dataset.py` — pulls one hardcoded event, writes raw JSON + CSVs.
- `liquidity_filter.py` — the filter logic described above, standalone.
- `siu_samford_raw.json`, `siu_samford_trades.csv`,
  `siu_samford_hourly_price_history.csv`, `siu_samford_liquidity_filtered.csv`
  — its output, committed.

**Generalized tool** (use this for any new game):
- `kalshi_game_report.py` — CLI: `--event TICKER` or `--search "team
  names"` (optionally `--series`), pulls trades/candlesticks/orderbook
  for both outcome markets, applies the liquidity filter, writes
  `kalshi_data/reports/<event_ticker>/{raw.json, trades.csv,
  hourly_price_history.csv, liquidity_filtered.csv, report.html}`, and
  (by default) upserts into the SQLite DB. `reports/` is gitignored —
  fully regenerable from the API.
- `report_template.html` — the HTML report template it fills in: KPI
  row, one price panel per team (raw dots + liquidity-filtered line +
  illiquid-hour shading + hover tooltips), a ten-biggest-trades diverging
  bar chart (auto-detects sweep clustering), methodology footnote. Colors
  come from the validated categorical palette (blue/orange), not team
  brand colors, so it's reusable across any matchup.
- `kalshi_db.py` — persistent SQLite store, schema: `events`, `markets`,
  `trades` (deduped by `trade_id`), `candlesticks` (upserted per
  market+hour), `orderbook_snapshots` (append-only time series). Nothing
  ever drops/truncates. `python3 kalshi_db.py` prints what's stored.
- `bulk_pull.py` — batch version: fetches every event in a date window
  (default: today .. +14 days) and pulls+persists each into the DB
  (DB-only by default, `--write-files` to also dump per-game CSVs). Each
  game is wrapped in try/except so one bad game doesn't kill the run.
  Idempotent, ~4.6s/game.
- `kalshi_market_data.db` — **the persistent dataset, checked into git**.
  This is the thing to build signals from.

## Published artifacts (Claude Artifacts, not in the repo)

Two chart reports were published this session as Claude Artifacts (URLs
are session-specific, listed here for reference — regenerate via
`kalshi_game_report.py` + republish if the links are dead in a new
session):
- "Homewood Line Watch" — SIU vs Samford (the original deep-dive,
  includes the Aug 25/26 and Aug 29 findings as callouts).
- "Illinois State vs Western Illinois" — the second game analyzed.

## Current DB state / in-flight work

As of this file being written, **`bulk_pull.py --start 2026-09-01 --end
2026-09-15` was running in the background**, targeting all 252
KXNCAAFGAME events in that window (Sept 5 and Sept 12 are the two big
Saturday slates, 103 and 113 games respectively). It was mid-run
(~83/252 done) when this file was written. **Check `git log` and
`python3 kalshi_data/kalshi_db.py` first** in a new session to see
whether it finished and was committed, or needs to be resumed/re-run
(safe to re-run — idempotent, will just pick up whatever's missing and
skip what's already there).

## Blocked / deferred items

- **New repo `claude-kalshi-market-view`**: user wants this code moved
  to its own private GitHub repo. Blocked because this session's GitHub
  integration can't create repos (403, scoped to pre-authorized repos
  only). User said they'll create the empty repo themselves "when on a
  computer" — once it exists, attach it and push everything here into
  it (and remove `kalshi_data/` from Backtester, per the user's earlier
  answer to that exact question).
- **CockroachDB / hosted DB**: discussed as the eventual real backing
  store (this repo already has a `psycopg2` + `.env` pattern for
  Postgres in `backtester/connect.py` that it would reuse — same driver,
  same config style). Needs the user to create a free CockroachDB
  Serverless cluster and hand over connection details via `.env` (not in
  chat). SQLite is the interim/current store; not yet migrated.

## Next phase: modeling signals (not started)

User's stated intent once the two-week dataset is in: start building
signals/features from the market flow data. Candidate feature ideas
raised in conversation (not yet implemented):
- Liquidity-filtered price vs. naive last price (the whole point of the
  filter — use the filtered series, not raw).
- Spread width as a confidence/liquidity signal.
- Volume / open-interest jumps as "real trade" indicators (already the
  `is_liquid` flag).
- Time-to-kickoff — line movement in the last 24–48h is likely more
  informative than early-week movement.
- Cross-check against a vig-removed sportsbook line for the same game
  (not implemented — no sportsbook data source wired up yet).
- YES(team A) + YES(team B) basis drift from 100¢ as a market-structure
  signal.
- Sweep detection (the top-10-trades clustering logic) as a feature:
  "was the move one big order or broad consensus."

No modeling code exists yet — this is the next thing to scope and build.
