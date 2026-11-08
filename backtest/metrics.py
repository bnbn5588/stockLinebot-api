"""Scoring for engine output.

Execution model: the signal for day t is only known after t's close (and
StockBot/LINE deliver it the next day before the US open), so every trade
and forward return is entered at the *next* session's open, t+1.
"""
from __future__ import annotations

from typing import Dict, List

import numpy as np
import pandas as pd

HORIZONS = [1, 5, 10, 20]
SIGNALS = ["BUY", "SELL", "HOLD"]


def forward_returns(px: pd.DataFrame, bench: pd.DataFrame, horizons: List[int] = HORIZONS) -> pd.DataFrame:
    """Per date t: return from open[t+1] to close[t+h], raw and in excess of
    the benchmark over the same window, in %."""
    out = pd.DataFrame(index=px.index)
    b = bench.reindex(px.index)
    for h in horizons:
        r = px.Close.shift(-h) / px.Open.shift(-1) - 1
        rb = b.Close.shift(-h) / b.Open.shift(-1) - 1
        out[f"r{h}"] = r * 100
        out[f"x{h}"] = (r - rb) * 100
    return out


def by_signal(panel: pd.DataFrame, horizons: List[int] = HORIZONS) -> pd.DataFrame:
    """Forward-return effectiveness per signal, pooled across tickers."""
    rows = []
    for s in SIGNALS:
        g = panel[panel.signal == s]
        row = {"signal": s, "n": len(g)}
        for h in horizons:
            row[f"r{h}"] = g[f"r{h}"].mean()
            row[f"x{h}"] = g[f"x{h}"].mean()
            row[f"hit{h}"] = (g[f"x{h}"] > 0).mean() * 100
        rows.append(row)
    return pd.DataFrame(rows).set_index("signal")


def calibration(panel: pd.DataFrame, h: int = 5) -> pd.DataFrame:
    """Does a higher confidence actually mean a better outcome? For BUY a
    good outcome is positive excess return; for SELL, negative."""
    bins = [-0.1, 0, 40, 50, 60, 101]
    labels = ["0", "0-40", "40-50", "50-60", "60+"]
    p = panel[panel.signal.isin(["BUY", "SELL"])].copy()
    p["bucket"] = pd.cut(p.confidence, bins=bins, labels=labels)
    p["edge"] = np.where(p.signal == "BUY", p[f"x{h}"], -p[f"x{h}"])
    g = p.groupby(["signal", "bucket"], observed=True)
    return pd.DataFrame({"n": g.size(), f"mean_x{h}": g[f"x{h}"].mean(), "hit%": g.edge.apply(lambda e: (e > 0).mean() * 100)})


def components(panel: pd.DataFrame, h: int = 5) -> pd.DataFrame:
    """Mean excess forward return when each indicator votes BUY vs SELL —
    a positive spread means the vote points the right way."""
    rows = []
    for c in [c for c in panel.columns if c.startswith("v_")]:
        buy = panel.loc[panel[c] == 1, f"x{h}"]
        sell = panel.loc[panel[c] == -1, f"x{h}"]
        rows.append({"indicator": c[2:], "n_buy": len(buy), f"x{h}_buy": buy.mean(),
                     "n_sell": len(sell), f"x{h}_sell": sell.mean(), "spread": buy.mean() - sell.mean()})
    if not rows:  # engine output carries no per-indicator votes
        return pd.DataFrame()
    return pd.DataFrame(rows).set_index("indicator").sort_values("spread", ascending=False)


def long_flat_equity(px: pd.DataFrame, sig: pd.Series, cost: float = 0.0) -> pd.Series:
    """Daily returns of the BUY-enter / SELL-exit / HOLD-keep strategy the
    AI worker's portfolio simulation assumes, executed at next open. `cost`
    is charged on every entry and every exit (fraction, 0.001 = 0.1%)."""
    sig = sig.reindex(px.index)
    pos = pd.Series(np.nan, index=px.index)
    pos[sig == "BUY"] = 1.0
    pos[sig == "SELL"] = 0.0
    pos = pos.ffill().fillna(0.0)
    # position decided at close t, held from open t+1
    held = pos.shift(1).fillna(0.0)
    entered = held.diff().fillna(held)
    # day of entry earns open->close, other held days close->close; exit at open
    cc = px.Close / px.Close.shift(1) - 1
    oc = px.Close / px.Open - 1
    co = px.Open / px.Close.shift(1) - 1
    exited = (held.shift(1).fillna(0) == 1) & (held == 0)
    ret = np.where(entered == 1, oc, np.where(held == 1, cc, 0.0))
    ret = np.where(exited, co, ret)
    ret = ret - cost * ((entered == 1) | exited)
    return pd.Series(ret, index=px.index).fillna(0.0)


def perf(daily: pd.Series) -> Dict[str, float]:
    eq = (1 + daily).cumprod()
    years = len(daily) / 252
    dd = eq / eq.cummax() - 1
    vol = daily.std() * np.sqrt(252)
    return {
        "total%": (eq.iloc[-1] - 1) * 100,
        "cagr%": (eq.iloc[-1] ** (1 / years) - 1) * 100 if years > 0 else np.nan,
        "maxdd%": dd.min() * 100,
        "sharpe": (daily.mean() * 252) / vol if vol else np.nan,
    }


def flips_per_year(sig: pd.Series) -> float:
    changes = (sig != sig.shift()).sum() - 1
    return changes / (len(sig) / 252)
