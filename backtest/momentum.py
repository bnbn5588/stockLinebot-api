"""Cross-sectional momentum vs the per-ticker v2 engine.

    python -m backtest.momentum --tickers <list> --start 2021-01-01

Rules fixed before testing (textbook definitions, not fitted to this data):
- score = 12-1 momentum: close 21 trading days ago / close 252 trading days
  ago - 1 (skipping the most recent month, per Jegadeesh-Titman)
- rebalance monthly on the last trading day; hold the top 25% by score,
  equal weight
- A1 "mom_top":      pure ranking, always fully invested
- A2 "mom_top_trend": same picks, but each slot is held only while that
  stock is above its 200-day average (otherwise that slot sits in cash),
  re-checked daily
- v2 baseline: equal-weight long/flat over every ticker, BUY enters /
  SELL exits (production api.stock_analysis.recommend_v2_frame)

Execution: weights decided at day d's close are applied from d+1's close
(one full day of delay), and every change in weight pays --cost per unit
turnover, for all strategies alike.

Predictive test, independent of portfolio construction: at each
rebalance, the Spearman rank correlation (IC) between score and the next
21-day return, and the top-quartile minus bottom-quartile spread.
"""
from __future__ import annotations

import argparse
from typing import Dict

import numpy as np
import pandas as pd

from api.stock_analysis import calculate_indicators_v2, recommend_v2_frame

from . import data, metrics
from .run import DEFAULT_TICKERS

LOOKBACK, SKIP, HOLD_FRAC = 252, 21, 0.25


def panel(tickers, period):
    px = data.load_many(tickers, period)
    close = pd.DataFrame({t: df.Close for t, df in px.items()}).sort_index()
    return close


def momentum_score(close: pd.DataFrame) -> pd.DataFrame:
    return close.shift(SKIP) / close.shift(LOOKBACK) - 1


def month_end_days(index: pd.DatetimeIndex) -> pd.DatetimeIndex:
    s = pd.Series(index, index=index)
    return pd.DatetimeIndex(s.groupby([index.year, index.month]).max().values)


def mom_weights(close: pd.DataFrame, trend_filter: bool) -> pd.DataFrame:
    score = momentum_score(close)
    rebal = month_end_days(close.index)
    picks = pd.DataFrame(0.0, index=close.index, columns=close.columns)
    current = pd.Series(0.0, index=close.columns)
    for d in close.index:
        if d in rebal:
            s = score.loc[d].dropna()
            if len(s) >= 4:
                k = max(1, int(round(len(s) * HOLD_FRAC)))
                current = pd.Series(0.0, index=close.columns)
                current[s.nlargest(k).index] = 1.0 / k
        picks.loc[d] = current
    if trend_filter:
        above = close > close.rolling(200).mean()
        picks = picks.where(above, 0.0)  # vacated slots stay in cash
    return picks


def v2_weights(close: pd.DataFrame, period: str) -> pd.DataFrame:
    w = pd.DataFrame(0.0, index=close.index, columns=close.columns)
    for t in close.columns:
        rec = recommend_v2_frame(calculate_indicators_v2(data.load(t, period)))
        pos = pd.Series(np.nan, index=rec.index)
        pos[rec.signal == "BUY"] = 1.0
        pos[rec.signal == "SELL"] = 0.0
        w[t] = pos.ffill().reindex(close.index).fillna(0.0)
    n_live = close.notna().sum(axis=1).clip(lower=1)
    return w.div(n_live, axis=0)


def equal_weight_bah(close: pd.DataFrame) -> pd.DataFrame:
    live = close.notna().astype(float)
    return live.div(live.sum(axis=1).clip(lower=1), axis=0)


def run_weights(close: pd.DataFrame, w: pd.DataFrame, cost: float) -> pd.Series:
    ret = close.pct_change().fillna(0.0)
    held = w.shift(1).fillna(0.0)  # decided at d's close, earns from d+1's close
    gross = (held.shift(1).fillna(0.0) * ret).sum(axis=1)
    turnover = held.diff().abs().sum(axis=1).fillna(held.abs().sum(axis=1))
    return gross - turnover * cost


def predictive(close: pd.DataFrame, bench: pd.Series) -> Dict[str, float]:
    score = momentum_score(close)
    fwd = close.shift(-SKIP) / close - 1
    fwd_b = bench.shift(-SKIP) / bench - 1
    ics, spreads, hits = [], [], []
    for d in month_end_days(close.index):
        s, f = score.loc[d], fwd.loc[d]
        ok = s.notna() & f.notna()
        if ok.sum() < 8:
            continue
        s, f = s[ok], f[ok]
        ics.append(s.rank().corr(f.rank()))
        q = max(1, int(round(len(s) * HOLD_FRAC)))
        top, bot = f[s.nlargest(q).index], f[s.nsmallest(q).index]
        spreads.append((top.mean() - bot.mean()) * 100)
        hits.append(((top - fwd_b.loc[d]) > 0).mean() * 100)
    ic = np.array(ics)
    return {
        "months": len(ic),
        "IC_mean": ic.mean(),
        "IC_t": ic.mean() / (ic.std(ddof=1) / np.sqrt(len(ic))) if len(ic) > 2 else np.nan,
        "IC>0_%": (ic > 0).mean() * 100,
        "top-bottom_21d%": np.mean(spreads),
        "top_beat_bench_%": np.mean(hits),
    }


def report(close, bench, weights, cost, start, end, label):
    win = (close.index >= start) & (close.index <= end)
    rows = {}
    for name, w in weights.items():
        daily = run_weights(close, w, cost)[win]
        p = metrics.perf(daily)
        held = w.shift(1).fillna(0.0)[win]
        p["exposure%"] = held.sum(axis=1).mean() * 100
        p["turnover/yr"] = held.diff().abs().sum(axis=1).sum() / (win.sum() / 252)
        rows[name] = p
    b = bench.pct_change().fillna(0.0)[win]
    rows["SPY"] = {**metrics.perf(b), "exposure%": 100.0, "turnover/yr": 0.0}
    print(f"\n-- {label}: {close.index[win][0].date()}..{close.index[win][-1].date()} (cost {cost * 100:.2f}%/turnover)")
    print(pd.DataFrame(rows).T)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--tickers", default=DEFAULT_TICKERS)
    ap.add_argument("--bench", default="SPY")
    ap.add_argument("--start", default="2021-01-01")
    ap.add_argument("--split", default="2025-01-01", help="out-of-sample start")
    ap.add_argument("--period", default="7y")
    ap.add_argument("--cost", type=float, default=0.001)
    a = ap.parse_args()

    tickers = a.tickers.split(",")
    close = panel(tickers, a.period)
    bench = data.load(a.bench, a.period).Close.reindex(close.index).ffill()
    weights = {
        "A1 mom_top": mom_weights(close, trend_filter=False),
        "A2 mom_top_trend": mom_weights(close, trend_filter=True),
        "v2 long/flat": v2_weights(close, a.period),
        "EW buy&hold": equal_weight_bah(close),
    }
    start, split = pd.Timestamp(a.start), pd.Timestamp(a.split)
    end = close.index[-1]
    print(f"universe: {len(tickers)} tickers | top {HOLD_FRAC:.0%} by 12-1 momentum, monthly")
    report(close, bench, weights, a.cost, start, end, "full")
    report(close, bench, weights, a.cost, start, split - pd.Timedelta(days=1), "in-sample")
    report(close, bench, weights, a.cost, split, end, "out-of-sample")

    win = close.index >= start
    print("\n-- predictive test (does rank predict next-month return?)")
    print({k: round(v, 3) for k, v in predictive(close[win], bench[win]).items()})


if __name__ == "__main__":
    main()
