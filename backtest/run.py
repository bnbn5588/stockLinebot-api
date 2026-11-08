"""Backtest one or more signal engines over a ticker universe.

    python -m backtest.run --engines current,current_1y --start 2021-01-01

Prints, per engine: forward-return effectiveness by signal (raw and excess
vs the benchmark), confidence calibration, per-indicator contribution, signal
churn, and a long/flat equal-weight portfolio vs buy-and-hold and the
benchmark. Signals are cached under .cache/ so re-runs with different
reporting options are instant.
"""
from __future__ import annotations

import argparse
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

import numpy as np
import pandas as pd

from . import data, metrics
from .engines import DERIVED, ENGINES

DEFAULT_TICKERS = "AAPL,ORCL,AMZN,MSFT,GOOGL,NVDA,AMD,TSM,MU,CDE,RXT,ASML,CRM,TGT"

pd.set_option("display.width", 160)
pd.set_option("display.max_columns", 30)
pd.set_option("display.float_format", lambda v: f"{v:,.2f}")


def _signals(engine: str, ticker: str, period: str, since: str) -> pd.DataFrame:
    px = data.load(ticker, period)
    path = data.CACHE_DIR / f"sig_{engine}_{ticker}_{since}_{px.index[-1].date()}.csv"
    if path.exists():
        return pd.read_csv(path, index_col="date", parse_dates=True)
    if engine in DERIVED:
        base, transform = DERIVED[engine]
        sig = transform(_signals(base, ticker, period, since), px)
    else:
        sig = ENGINES[engine](px, since=since)
    sig.to_csv(path)
    return sig


def _job(args):
    engine, ticker, period, since = args
    return engine, ticker, _signals(engine, ticker, period, since)


def build_panel(engines, tickers, period, jobs, since):
    data.load_many(tickers, period)  # download serially; Yahoo dislikes bursts
    with ProcessPoolExecutor(max_workers=jobs) as ex:
        results = list(ex.map(_job, [(e, t, period, since) for e in engines for t in tickers]))
    return {(e, t): s for e, t, s in results}


def report(engine, tickers, sigs, prices, bench, start, end):
    frames, strat, bah, churn, trades = [], [], [], [], []
    for t in tickers:
        s = sigs[(engine, t)]
        s = s[(s.index >= start) & (s.index <= end)]
        if s.empty:
            continue
        px = prices[t].loc[s.index[0]:end]
        f = metrics.forward_returns(px, bench).reindex(s.index)
        frames.append(s.join(f).assign(ticker=t))
        strat.append(metrics.long_flat_equity(px, s.signal).rename(t))
        bah.append((px.Close.pct_change().fillna(0)).rename(t))
        churn.append(metrics.flips_per_year(s.signal))
        # completed BUY->SELL round trips, like the AI worker's trade stats
        state = s.signal.replace("HOLD", np.nan).ffill()
        trades.append(((state == "BUY") & (state.shift(-1) == "SELL")).sum())
    panel = pd.concat(frames)

    print(f"\n{'=' * 30} engine: {engine} {'=' * 30}")
    print(f"rows={len(panel)}  tickers={len(frames)}  window={start.date()}..{panel.index.max().date()}")
    mix = panel.signal.value_counts(normalize=True).mul(100).round(1).to_dict()
    print(f"signal mix %: {mix}  |  flips/ticker/yr: {np.mean(churn):.1f}  |  "
          f"round trips/ticker: {np.mean(trades):.1f}")
    print(f"confidence: max={panel.confidence.max():.1f}  >=60: {(panel.confidence >= 60).mean() * 100:.1f}%  "
          f">=70: {(panel.confidence >= 70).mean() * 100:.1f}%")

    print("\n-- forward returns by signal (r = raw %, x = excess vs bench %, hit = % beating bench)")
    print(metrics.by_signal(panel))
    print("\n-- confidence calibration (5d)")
    print(metrics.calibration(panel))
    print("\n-- indicator votes: mean 5d excess when voting BUY vs SELL")
    print(metrics.components(panel))

    sd = pd.concat(strat, axis=1).fillna(0).mean(axis=1)
    bd = pd.concat(bah, axis=1).fillna(0).mean(axis=1)
    bench_d = bench.Close.pct_change().reindex(sd.index).fillna(0)
    perf = pd.DataFrame({
        "strategy (EW long/flat)": metrics.perf(sd),
        "buy&hold (EW)": metrics.perf(bd),
        "benchmark": metrics.perf(bench_d),
    }).T
    print("\n-- portfolio (equal-weight across tickers, entries/exits at next open)")
    print(perf)
    return panel, perf


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--engines", default="current,current_1y")
    ap.add_argument("--tickers", default=DEFAULT_TICKERS)
    ap.add_argument("--bench", default="SPY")
    ap.add_argument("--since", default="2021-01-01", help="first date to compute signals for (cache key)")
    ap.add_argument("--start", default="2021-01-01", help="report window start (>= --since)")
    ap.add_argument("--end", default="2100-01-01")
    ap.add_argument("--period", default="7y", help="history to download")
    ap.add_argument("--jobs", type=int, default=2)
    ap.add_argument("--out", default=None, help="dir to write per-engine panel CSVs")
    a = ap.parse_args()

    engines = a.engines.split(",")
    tickers = a.tickers.split(",")
    unknown = set(engines) - set(ENGINES) - set(DERIVED)
    if unknown:
        raise SystemExit(f"unknown engine(s): {unknown}; have {list(ENGINES) + list(DERIVED)}")

    prices = data.load_many(tickers + [a.bench], a.period)
    bench = prices[a.bench]
    sigs = build_panel(engines, tickers, a.period, a.jobs, a.since)
    start, end = pd.Timestamp(a.start), pd.Timestamp(a.end)

    for e in engines:
        panel, _ = report(e, tickers, sigs, prices, bench, start, end)
        if a.out:
            Path(a.out).mkdir(parents=True, exist_ok=True)
            panel.to_csv(Path(a.out) / f"panel_{e}.csv")


if __name__ == "__main__":
    main()
