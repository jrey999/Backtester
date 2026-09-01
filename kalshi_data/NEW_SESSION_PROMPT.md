# Paste this into a new session started on `jrey999/kalshi-market-view`

---

We're building a Kalshi prediction-market data pipeline and analysis tool for
college football, working toward modeling signals from order flow. The code
currently lives in a different repo and needs to move here as your first task.

## First task: migrate the code

All the code is in `jrey999/Backtester`, on branch
`claude/siu-samford-kalshi-data-59r9tn`, in the `kalshi_data/` directory.
Attach that repo, and move these files into the root of this repo
(`kalshi-market-view`), adjusting any internal paths that assume a
`kalshi_data/` prefix:

- `kalshi_game_report.py` — main per-game CLI
- `report_template.html` — HTML chart report template
- `kalshi_db.py` — SQLite store
- `bulk_pull.py` — batch pull over a date window
- `spaces_export.py` — SQLite → Parquet in the bucket layout
- `spaces_sync.py` — upload to DigitalOcean Spaces
- `liquidity_filter.py`, `build_dataset.py` — original one-off scripts
- `CONTEXT.md` — **read this first, it has all the technical detail**
- `.gitignore` entries for `.env`, `*.db`, `*.db-journal`/`-wal`/`-shm`,
  `spaces/`, `reports/`

Do NOT bring the git history over — the old branch has a ~22 MB SQLite file
committed in it, and starting clean is the point of the move. Data does not
belong in this repo at all; the Spaces bucket is the system of record.

## What this project is

Kalshi runs binary markets on individual college football games (series
`KXNCAAFGAME`, two outcome markets per game, one per team). We pull trades,
hourly candlesticks, and orderbook snapshots, store them, and analyze how
money moves before and during games.

**The central finding so far**: these markets are extremely illiquid, and the
"last traded price" is often meaningless — a single $1–3 lot crossing a wide,
stale spread moves the printed price 25¢ while the actual quoted book doesn't
budge. Everything is built around separating real trades from that noise. The
liquidity filter and its rationale are documented in `CONTEXT.md`; read it
before touching the analysis code, because the filter's design (two
independent checks, deliberately not ANDed) is easy to "simplify" incorrectly.

## Architecture in one paragraph

SQLite is a local working cache that absorbs the incremental churn of pulls.
The DigitalOcean Spaces bucket (`degenerate-cafe`, nyc3) is the system of
record, holding Parquet partitioned as
`kalshi/sport=cfb/season=<yyyy>/{events,markets}.parquet` plus
`{trades,candlesticks,orderbooks}/week=<iso-year-week>/*.parquet`. The repo
holds code only. Credentials live in a gitignored `.env`
(`SPACES_KEY`, `SPACES_SECRET`, `SPACES_REGION=nyc3`,
`SPACES_BUCKET=degenerate-cafe`) — I'll provide them, don't ask me to paste
them in chat if a `.env` already exists.

## Environment notes

- `pip install pyarrow boto3` — not present by default. `python-dotenv` is
  also absent, which is why `spaces_sync.py` parses `.env` itself.
- Outbound network is allowlisted per environment. Open:
  `api.elections.kalshi.com`, `nyc3.digitaloceanspaces.com`,
  `api.collegefootballdata.com`. Blocked (403 from the proxy): ESPN,
  TheOddsAPI, TheSportsDB, Sportradar, and Kalshi's docs domains. If you hit
  a 403 from the proxy, report it rather than trying to route around it.
- The container is ephemeral. Anything not pushed to git or synced to Spaces
  is lost when it's reclaimed.

## Where things stand

- Season 2026 (the current two-week CFB slate, 252 games) is fully pulled and
  uploaded to Spaces.
- Season 2025 backfill was in progress (~234 of 936 games, 1.24M trades) when
  the previous session ended. **Check whether it completed.** It's resumable:
  `python3 bulk_pull.py --start 2025-08-01 --end 2026-02-01 --db kalshi_historical.db --skip-existing`
  then `spaces_export.py` + `spaces_sync.py`. It takes a few hours; run it in
  the background.
- Kalshi's `/historical/*` endpoints serve settled games AND include a
  `result` field ("yes"/"no") — that's ground-truth win/loss labels for free,
  no external results API needed.

## What's next

Modeling signals. Nothing is built yet. The plan:

1. **Calibration first** — bucket games by closing price and check whether
   games priced at 90% actually win ~90% of the time. Cheap, and it tells us
   whether there's mispricing worth chasing before isolating features.
2. **Then predictive** — does an order-flow signal (a real sweep, the
   liquidity-filtered price diverging from the naive last price, direction of
   the biggest trades) beat the pre-game price at picking winners?

Start by confirming the backfill state and getting the full 2025 season into
Spaces, since everything downstream needs that data. Then we'll scope the
calibration work together — don't build the whole modeling stack unprompted.

## Working style

Push back when I'm wrong rather than agreeing — earlier in this project I
misread a stale quote as a real price move and the correction mattered. If
the data says something different from what I assert, say so and show me
the numbers.
