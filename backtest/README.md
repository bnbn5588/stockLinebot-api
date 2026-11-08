# Backtest harness

Replays the signal engine over years of daily data to measure whether its
signals actually predict anything — before changing production.

- `engines.py` — engines under test. `current` imports
  `calculate_indicators` / `generate_recommendation` from
  `api/stock_analysis.py` and replays them per day on the same trailing
  90-calendar-day window StockBot requests (`period: "90d"`), so it describes
  the live system. `fast_indicators()` is a vectorized copy of
  `calculate_indicators()` used for speed; `check_fast_indicators()` verifies
  they match exactly. Candidate engines (`v2_*`, `current_gated`) live here too.
- `metrics.py` — forward returns (entered at the **next open**, raw and in
  excess of the benchmark), confidence calibration, per-indicator vote
  quality, signal churn, and a long/flat portfolio (BUY enter, SELL exit,
  HOLD keep — same semantics as the AI worker's simulation).
- `run.py` — runs engines over a universe and prints the report.
- `parity.py` — compares the `current` replay with what production actually
  logged to the StockBot `history` sheet, broken down by API era.

## Run

Host Python is too old; use the image (code is mounted, so edits need no rebuild):

```
docker build -t stock-backtest -f backtest/Dockerfile .
docker run --rm -e PYTHONWARNINGS=ignore -v "$PWD":/app stock-backtest \
  python -m backtest.run --engines current,v2_trend --start 2021-01-01
# out-of-sample slice, reusing cached signals:
docker run ... python -m backtest.run --engines current,v2_trend --start 2025-01-01
# parity vs the live sheet:
docker run ... -e SHEET_ID=<sheet id> stock-backtest python -m backtest.parity
```

`--since` is the first date signals are computed for (part of the cache key);
`--start`/`--end` only change the reporting window. Price data and signals are
cached in `backtest/.cache/` (gitignored); delete it to re-download.

The `current` replay costs one full indicator recompute per ticker per day
(~0.1 s each on the OCI host), so a 15-ticker, 6-year run takes ~30 min per
engine on 2 cores the first time; cached afterwards. Candidate engines compute
once on full history and take seconds.

## Caveats

- Yahoo re-adjusts old bars for later splits/dividends, so replayed values can
  differ slightly from what the API saw on the day.
- The default universe is today's watchlist — chosen with hindsight and
  heavy in winners. Re-run on a broader list (`--tickers`) before trusting a
  result.
