"""
Export the SQLite working cache into the file layout used by the
DigitalOcean Spaces bucket, as Parquet.

The bucket is the system of record for the full historical dataset --
it's far too large for git, and SQLite is only a local working cache.
This writes the same tree locally first so an upload is a plain sync
(see spaces_sync.py), which also means the export is useful on its own
even before any credentials exist.

Layout (mirrored exactly in the bucket under the kalshi/ prefix):

    kalshi/
      events/       season=<yyyy>/series=<ticker>/events.parquet
      markets/      season=<yyyy>/series=<ticker>/markets.parquet
      trades/       season=<yyyy>/event=<event_ticker>/trades.parquet
      candlesticks/ season=<yyyy>/event=<event_ticker>/candles.parquet
      orderbooks/   season=<yyyy>/event=<event_ticker>/snapshots.parquet

Partitioning by season/event keeps each game independently writable, so a
failed or re-pulled game rewrites exactly one prefix instead of a monolith.
Parquet buys ~5-10x over raw JSON plus column pruning, which matters at the
tens-of-millions-of-trades scale a full season reaches.

Usage:
  python3 spaces_export.py --db kalshi_data/kalshi_historical.db
  python3 spaces_export.py --db kalshi_data/kalshi_market_data.db --out kalshi_data/spaces
"""
import argparse
import os
import re
import sys

import pyarrow as pa
import pyarrow.parquet as pq

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import kalshi_db

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
DEFAULT_OUT = os.path.join(SCRIPT_DIR, "spaces")

MONTHS = {"JAN": 1, "FEB": 2, "MAR": 3, "APR": 4, "MAY": 5, "JUN": 6,
          "JUL": 7, "AUG": 8, "SEP": 9, "OCT": 10, "NOV": 11, "DEC": 12}


def season_of(event_ticker):
    """A college football season spans Aug -> Jan, so games in Jan belong to
    the previous calendar year's season (a Jan 2026 playoff game is 2025)."""
    m = re.search(r"-(\d{2})([A-Z]{3})(\d{2})", event_ticker)
    if not m:
        return "unknown"
    yy, mon, _ = m.groups()
    year = 2000 + int(yy)
    month = MONTHS.get(mon, 1)
    return str(year if month >= 7 else year - 1)


def write_parquet(rows, columns, path):
    if not rows:
        return 0
    os.makedirs(os.path.dirname(path), exist_ok=True)
    table = pa.table({col: [r[i] for r in rows] for i, col in enumerate(columns)})
    pq.write_table(table, path, compression="snappy")
    return len(rows)


def export(db_path, out_root):
    # via kalshi_db so any pending schema migration is applied first -- a DB
    # written before status/result existed would otherwise fail the SELECT below
    conn = kalshi_db.connect(db_path)

    events = conn.execute(
        "SELECT event_ticker, series_ticker, title, sub_title, first_pulled_at, last_pulled_at FROM events"
    ).fetchall()
    if not events:
        print(f"No events in {db_path}; nothing to export.")
        return

    # events + markets are small and queried across games, so they group by
    # season/series rather than per-event.
    by_season_series = {}
    for row in events:
        key = (season_of(row[0]), row[1])
        by_season_series.setdefault(key, []).append(row)

    n_files = n_rows = 0
    for (season, series), rows in by_season_series.items():
        path = f"{out_root}/kalshi/events/season={season}/series={series}/events.parquet"
        n = write_parquet(rows, ["event_ticker", "series_ticker", "title", "sub_title",
                                 "first_pulled_at", "last_pulled_at"], path)
        n_files += 1
        n_rows += n

        tickers = tuple(r[0] for r in rows)
        placeholders = ",".join("?" * len(tickers))
        markets = conn.execute(
            f"""SELECT market_ticker, event_ticker, label, open_time, close_time,
                       status, result, last_pulled_at
                FROM markets WHERE event_ticker IN ({placeholders})""", tickers
        ).fetchall()
        path = f"{out_root}/kalshi/markets/season={season}/series={series}/markets.parquet"
        n = write_parquet(markets, ["market_ticker", "event_ticker", "label", "open_time",
                                    "close_time", "status", "result", "last_pulled_at"], path)
        n_files += 1
        n_rows += n

    # trades / candlesticks / orderbooks partition per event
    for (event_ticker,) in conn.execute("SELECT event_ticker FROM events").fetchall():
        season = season_of(event_ticker)

        trades = conn.execute(
            """SELECT trade_id, market_ticker, event_ticker, created_time,
                      yes_price_cents, no_price_cents, size, taker_side
               FROM trades WHERE event_ticker=? ORDER BY created_time""", (event_ticker,)
        ).fetchall()
        if trades:
            path = f"{out_root}/kalshi/trades/season={season}/event={event_ticker}/trades.parquet"
            n_rows += write_parquet(trades, ["trade_id", "market_ticker", "event_ticker",
                                             "created_time", "yes_price_cents", "no_price_cents",
                                             "size", "taker_side"], path)
            n_files += 1

        candles = conn.execute(
            """SELECT c.market_ticker, c.end_period_ts, c.open_cents, c.close_cents,
                      c.high_cents, c.low_cents, c.yes_bid_close_cents,
                      c.yes_ask_close_cents, c.volume, c.open_interest
               FROM candlesticks c JOIN markets m ON m.market_ticker = c.market_ticker
               WHERE m.event_ticker=? ORDER BY c.market_ticker, c.end_period_ts""", (event_ticker,)
        ).fetchall()
        if candles:
            path = f"{out_root}/kalshi/candlesticks/season={season}/event={event_ticker}/candles.parquet"
            n_rows += write_parquet(candles, ["market_ticker", "end_period_ts", "open_cents",
                                              "close_cents", "high_cents", "low_cents",
                                              "yes_bid_close_cents", "yes_ask_close_cents",
                                              "volume", "open_interest"], path)
            n_files += 1

        books = conn.execute(
            """SELECT o.market_ticker, o.pulled_at, o.raw_json
               FROM orderbook_snapshots o JOIN markets m ON m.market_ticker = o.market_ticker
               WHERE m.event_ticker=? ORDER BY o.pulled_at""", (event_ticker,)
        ).fetchall()
        if books:
            path = f"{out_root}/kalshi/orderbooks/season={season}/event={event_ticker}/snapshots.parquet"
            n_rows += write_parquet(books, ["market_ticker", "pulled_at", "raw_json"], path)
            n_files += 1

    conn.close()
    print(f"Exported {n_rows:,} rows across {n_files:,} parquet files to {out_root}/kalshi/")


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--db", required=True, help="SQLite DB to export from")
    parser.add_argument("--out", default=DEFAULT_OUT, help=f"Local root for the bucket tree (default {DEFAULT_OUT})")
    args = parser.parse_args()
    export(args.db, args.out)


if __name__ == "__main__":
    main()
