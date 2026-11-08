"""Signal engines under test.

Every engine takes one ticker's full daily OHLCV history and returns, for
each date it can score, the signal the API *would have returned* had it been
called after that day's close — strictly point-in-time, using only bars up
to and including that date.

`current` replays production exactly by importing calculate_indicators() and
generate_recommendation() straight from api/stock_analysis.py and feeding
them the same trailing calendar window the API downloads (StockBot calls it
with period="90d"). Because production recomputes EMA/ADX from scratch on
that short window each call, the window length itself changes the output —
so the replay slices it per date instead of computing once on full history.
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Callable, Dict

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from api.stock_analysis import (  # noqa: E402
    calculate_indicators, calculate_indicators_v2, generate_recommendation, recommend_v2_frame,
)

REQUIRED_COLS = ['SMA20', 'SMA50', 'EMA20', 'EMA50', 'RSI', 'MACD',
                 'MACD_Signal', 'ATR14', 'BB_Upper', 'BB_Lower',
                 'Stoch_K', 'Stoch_D', 'OBV', 'OBV_EMA20', 'ADX']

# Per-indicator votes in generate_recommendation(), mirrored here (+1 = BUY
# vote, -1 = SELL vote, 0 = abstain) so the harness can measure what each
# component contributes. Keep in sync with the API if its rules change.
VOTES: Dict[str, Callable[[pd.Series], int]] = {
    "ema":   lambda r: int(r.EMA20 > r.EMA50) - int(r.EMA20 < r.EMA50),
    "macd":  lambda r: int(r.MACD > r.MACD_Signal) - int(r.MACD < r.MACD_Signal),
    "rsi":   lambda r: int(r.RSI < 30) - int(r.RSI > 70),
    "bb":    lambda r: int(r.Close < r.BB_Lower) - int(r.Close > r.BB_Upper),
    "stoch": lambda r: int(r.Stoch_K < 20) - int(r.Stoch_K > 80),
    "obv":   lambda r: int(r.OBV > r.OBV_EMA20) - int(r.OBV < r.OBV_EMA20),
    "sma":   lambda r: int(r.SMA20 > r.SMA50) - int(r.SMA20 < r.SMA50),
    "di":    lambda r: int(r.Plus_DI > r.Minus_DI) - int(r.Plus_DI < r.Minus_DI),
}


def fast_indicators(data: pd.DataFrame) -> pd.DataFrame:
    """Numerically identical to api.stock_analysis.calculate_indicators()
    (verified by check_fast_indicators) but with the per-row OBV loop
    vectorized — that loop dominates replay time, since the harness recomputes
    indicators once per ticker per day."""
    data = data.copy()
    c, h, l, v = data["Close"], data["High"], data["Low"], data["Volume"]
    data['SMA20'] = c.rolling(20).mean()
    data['SMA50'] = c.rolling(50).mean()
    data['EMA20'] = c.ewm(span=20, adjust=False).mean()
    data['EMA50'] = c.ewm(span=50, adjust=False).mean()
    delta = c.diff()
    rs = delta.clip(lower=0).rolling(14).mean() / (-delta.clip(upper=0)).rolling(14).mean()
    data['RSI'] = 100 - (100 / (1 + rs))
    data['MACD'] = c.ewm(span=12, adjust=False).mean() - c.ewm(span=26, adjust=False).mean()
    data['MACD_Signal'] = data['MACD'].ewm(span=9, adjust=False).mean()
    tr = pd.concat([h - l, (h - c.shift()).abs(), (l - c.shift()).abs()], axis=1).max(axis=1)
    data['ATR14'] = tr.rolling(14).mean()
    data['VMA20'] = v.rolling(20).mean()
    std20 = c.rolling(20).std()
    data['BB_Middle'] = data['SMA20']
    data['BB_Upper'] = data['SMA20'] + 2 * std20
    data['BB_Lower'] = data['SMA20'] - 2 * std20
    ll, hh = l.rolling(14).min(), h.rolling(14).max()
    data['Stoch_K'] = (c - ll) / (hh - ll) * 100
    data['Stoch_D'] = data['Stoch_K'].rolling(3).mean()
    step = np.sign(c.diff()).fillna(0) * v
    step.iloc[0] = 0
    data['OBV'] = step.cumsum()
    data['OBV_EMA20'] = data['OBV'].ewm(span=20, adjust=False).mean()
    up, down = h - h.shift(), l.shift() - l
    plus_dm = up.where((up > down) & (up > 0), 0.0)
    minus_dm = down.where((down > up) & (down > 0), 0.0)
    atr_s = tr.ewm(span=14, adjust=False).mean()
    plus_di = 100 * (plus_dm.ewm(span=14, adjust=False).mean() / atr_s)
    minus_di = 100 * (minus_dm.ewm(span=14, adjust=False).mean() / atr_s)
    dx = (abs(plus_di - minus_di) / (plus_di + minus_di)) * 100
    data['ADX'] = dx.ewm(span=14, adjust=False).mean()
    data['Plus_DI'] = plus_di
    data['Minus_DI'] = minus_di
    return data


def check_fast_indicators(df: pd.DataFrame, samples: int = 25) -> float:
    """Max abs difference between fast_indicators() and the production
    function over random trailing windows. Should be ~1e-9 or less."""
    rng = np.random.default_rng(0)
    worst = 0.0
    for end in rng.integers(120, len(df), samples):
        win = df.iloc[end - 62:end]
        a = calculate_indicators(win.copy(), "_")
        b = fast_indicators(win)
        cols = [c for c in a.columns if c in b.columns]
        worst = max(worst, float((a[cols] - b[cols]).abs().max().max()))
    return worst


def current(df: pd.DataFrame, window_days: int = 90, since=None) -> pd.DataFrame:
    """Production logic, replayed day by day on a trailing `window_days`
    calendar window (90 = what StockBot requests today). `since` skips
    scoring dates before it (each date costs a full indicator recompute)."""
    out = []
    start = df.index[0] + pd.Timedelta(days=window_days)
    if since is not None:
        start = max(start, pd.Timestamp(since))
    for t in df.index[df.index >= start]:
        win = df.loc[(df.index > t - pd.Timedelta(days=window_days)) & (df.index <= t)].copy()
        ind = fast_indicators(win)
        ind = ind.dropna(subset=REQUIRED_COLS)
        if ind.empty:
            continue
        latest = ind.iloc[-1]
        signal, strength, conf, trend, adx = generate_recommendation(latest, "_")
        row = {"date": t, "signal": signal, "confidence": conf, "strength": strength, "adx": adx}
        row.update({f"v_{k}": f(latest) for k, f in VOTES.items()})
        out.append(row)
    return pd.DataFrame(out).set_index("date")


# ── Candidate engines ───────────────────────────────────────────────────────
# Computed once on full history: every series below is causal (row t uses
# bars <= t only) and, with years of history, fully warmed up, so no per-day
# replay is needed. Rules are textbook regime/trend definitions, deliberately
# NOT tuned on this data — the point is to test structure, not fit params.

def _wilder(x: pd.Series, n: int = 14) -> pd.Series:
    return x.ewm(alpha=1 / n, adjust=False).mean()


def standard_indicators(df: pd.DataFrame) -> pd.DataFrame:
    d = df.copy()
    c, h, l = d.Close, d.High, d.Low
    d["SMA50"] = c.rolling(50).mean()
    d["SMA200"] = c.rolling(200).mean()
    d["EMA20"] = c.ewm(span=20, adjust=False).mean()
    d["EMA50"] = c.ewm(span=50, adjust=False).mean()
    delta = c.diff()
    d["RSI"] = 100 - 100 / (1 + _wilder(delta.clip(lower=0)) / _wilder(-delta.clip(upper=0)))
    macd = c.ewm(span=12, adjust=False).mean() - c.ewm(span=26, adjust=False).mean()
    d["MACD_H"] = macd - macd.ewm(span=9, adjust=False).mean()
    tr = pd.concat([h - l, (h - c.shift()).abs(), (l - c.shift()).abs()], axis=1).max(axis=1)
    atr = _wilder(tr)
    up, down = h - h.shift(), l.shift() - l
    pdi = 100 * _wilder(up.where((up > down) & (up > 0), 0.0)) / atr
    mdi = 100 * _wilder(down.where((down > up) & (down > 0), 0.0)) / atr
    d["Plus_DI"], d["Minus_DI"] = pdi, mdi
    d["ADX"] = _wilder((pdi - mdi).abs() / (pdi + mdi) * 100)
    d["SMA20"] = c.rolling(20).mean()
    d["BB_Lower"] = d.SMA20 - 2 * c.rolling(20).std()
    return d.dropna(subset=["SMA200", "ADX", "RSI"])


def _emit(d: pd.DataFrame, buy: pd.Series, sell: pd.Series, conf: pd.Series, since) -> pd.DataFrame:
    sig = pd.Series("HOLD", index=d.index)
    sig[buy] = "BUY"
    sig[sell & ~buy] = "SELL"
    out = pd.DataFrame({"signal": sig, "confidence": conf.round(1), "adx": d.ADX.round(2)})
    out["v_regime"] = np.sign(d.Close - d.SMA200).astype(int)
    out["v_di"] = np.sign(d.Plus_DI - d.Minus_DI).astype(int)
    out["v_macd"] = np.sign(d.MACD_H).astype(int)
    out["v_rsi"] = ((d.RSI < 30).astype(int) - (d.RSI > 70).astype(int))
    out.index.name = "date"
    return out[out.index >= pd.Timestamp(since)] if since is not None else out


def _agreement(d: pd.DataFrame) -> pd.Series:
    """Share (0-100) of trend components pointing the same way as the
    regime — a plain, interpretable confidence; calibration is checked by
    the harness rather than assumed."""
    votes = pd.concat([np.sign(d.Close - d.SMA200), np.sign(d.SMA50 - d.SMA200),
                       np.sign(d.EMA20 - d.EMA50), np.sign(d.Plus_DI - d.Minus_DI),
                       np.sign(d.MACD_H)], axis=1)
    return votes.mean(axis=1).abs() * 100


def v2_trend(df: pd.DataFrame, since=None) -> pd.DataFrame:
    """Production v2 (api.stock_analysis.recommend_v2_frame), scored on full
    history in one pass — v2's indicators are causal, so this equals calling
    the API after each day's close. Votes are attached for the report."""
    ind = calculate_indicators_v2(df)
    rec = recommend_v2_frame(ind)
    d = standard_indicators(df).reindex(rec.index)
    out = _emit(d, rec.signal == "BUY", rec.signal == "SELL", rec.confidence, since)
    out["strength"] = rec.strength.reindex(out.index)
    return out


# ── v2 refinements (B: market regime, C: whipsaw control) ──────────────────
# Fixed before testing; judged on the broad universe with costs.

def _market_up(index: pd.DatetimeIndex) -> pd.Series:
    from . import data as _data  # lazy: engines must import without the cache
    spy = _data.load("SPY").Close
    return (spy > spy.rolling(200).mean()).reindex(index).ffill().fillna(False)


def _confirm(sig: pd.Series, days: int = 2) -> pd.Series:
    """A new signal takes effect only after it has shown `days` in a row."""
    out, emitted = [], None
    streak, last = 0, None
    for s in sig:
        streak = streak + 1 if s == last else 1
        last = s
        if emitted is None or (s != emitted and streak >= days):
            emitted = s
        out.append(emitted)
    return pd.Series(out, index=sig.index)


def _v2_signals(df: pd.DataFrame, since=None, band: float = 0.0,
                market: str = "", confirm: int = 1) -> pd.DataFrame:
    ind = calculate_indicators_v2(df)
    rec = recommend_v2_frame(ind)
    d = ind.loc[rec.index]
    if band:
        # C2: enter only clearly above SMA200, exit only clearly below
        up_trend = (d.EMA20 > d.EMA50) & (d.Plus_DI > d.Minus_DI)
        down_trend = (d.EMA20 < d.EMA50) & (d.Minus_DI > d.Plus_DI)
        buy = (d.Close > d.SMA200 * (1 + band)) & up_trend
        sell = (d.Close < d.SMA200 * (1 - band)) | down_trend
        sig = pd.Series("HOLD", index=d.index)
        sig[buy] = "BUY"
        sig[sell & ~buy] = "SELL"
    else:
        sig = rec.signal.copy()
    if market:
        up = _market_up(sig.index)
        if market == "gate":      # B1: no new BUY in a market downtrend
            sig[(sig == "BUY") & ~up] = "HOLD"
        elif market == "exit":    # B2: market downtrend -> everything SELL
            sig[~up] = "SELL"
    if confirm > 1:
        sig = _confirm(sig, confirm)
    out = pd.DataFrame({"signal": sig, "confidence": rec.confidence, "adx": rec.adx})
    out.index.name = "date"
    return out[out.index >= pd.Timestamp(since)] if since is not None else out


def v2_pullback(df: pd.DataFrame, since=None) -> pd.DataFrame:
    """Buy dips inside an uptrend: oversold RSI or a lower-Bollinger tag
    while above the 200-day average; exit on losing the 200-day average.
    Mean-reversion indicators used only for timing, never against trend."""
    d = standard_indicators(df)
    up_regime = (d.Close > d.SMA200) & (d.SMA50 > d.SMA200)
    buy = up_regime & ((d.RSI < 40) | (d.Close < d.BB_Lower))
    sell = d.Close < d.SMA200
    return _emit(d, buy, sell, _agreement(d), since)


def sma200_gate(sig: pd.DataFrame, df: pd.DataFrame) -> pd.DataFrame:
    """Smallest change to production: keep the 7-indicator vote, but a SELL
    is only allowed below the 200-day average and a BUY only above it —
    otherwise HOLD. Applied on top of an existing engine's output."""
    sig = sig.copy()
    sma200 = df.Close.rolling(200).mean().reindex(sig.index)
    above = df.Close.reindex(sig.index) > sma200
    sig.loc[(sig.signal == "SELL") & above, "signal"] = "HOLD"
    sig.loc[(sig.signal == "BUY") & ~above, "signal"] = "HOLD"
    return sig


def v0_from_votes(sig: pd.DataFrame, df: pd.DataFrame) -> pd.DataFrame:
    """The original engine (Aug 2025 -> 2026-04-16, commits 539bf1b/0c7ada2):
    an unweighted vote of SMA20/50, EMA20/50, RSI 30/70 and MACD vs signal
    (its volume term added 0.5 to both sides, so it never mattered), ties ->
    HOLD, no confidence score. Its indicator formulas are identical to v1's
    and StockBot always requested period=90d, so it's derived from the v1
    replay's cached per-indicator votes instead of a second replay."""
    out = sig.copy()
    total = sig.v_sma + sig.v_ema + sig.v_rsi + sig.v_macd
    out["signal"] = np.where(total > 0, "BUY", np.where(total < 0, "SELL", "HOLD"))
    out["confidence"] = 0.0
    out["strength"] = ""
    return out


# name -> (base engine, transform(base_signals, full_history_df))
DERIVED = {
    "v0": ("current", v0_from_votes),
    "current_gated": ("current_1y", sma200_gate),
}

# name -> fn(full_history_df, since) -> signals from `since` onward
ENGINES: Dict[str, Callable[..., pd.DataFrame]] = {
    "current": lambda df, since=None: current(df, 90, since),
    # Same rules, longer window so EMA50/ADX have warmed up (proposal C).
    "current_1y": lambda df, since=None: current(df, 365, since),
    "v2_trend": v2_trend,
    "v2_pullback": v2_pullback,
    "v2_B1_mktgate": lambda df, since=None: _v2_signals(df, since, market="gate"),
    "v2_B2_mktexit": lambda df, since=None: _v2_signals(df, since, market="exit"),
    "v2_C1_confirm2": lambda df, since=None: _v2_signals(df, since, confirm=2),
    "v2_C2_band2": lambda df, since=None: _v2_signals(df, since, band=0.02),
    "v2_B1C1": lambda df, since=None: _v2_signals(df, since, market="gate", confirm=2),
}
