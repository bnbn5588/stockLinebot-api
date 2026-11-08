"""Daily OHLCV download + on-disk cache for the backtest.

Same source and adjustment as production (yfinance, auto_adjust=True), so the
indicators the harness computes match what the API would have computed on
that day, up to Yahoo's later split/dividend re-adjustments of old bars.
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Dict, List

import pandas as pd
import yfinance as yf

CACHE_DIR = Path(os.getenv("BACKTEST_CACHE", Path(__file__).parent / ".cache"))
_OHLCV = ["Open", "High", "Low", "Close", "Volume"]


def _download(ticker: str, period: str) -> pd.DataFrame:
    df = yf.download(ticker, period=period, interval="1d", auto_adjust=True,
                     progress=False, multi_level_index=False)
    if df is None or df.empty:
        raise RuntimeError(f"no data for {ticker}")
    df = df[_OHLCV].dropna(subset=["Close"])
    df.index = pd.to_datetime(df.index).tz_localize(None).normalize()
    df.index.name = "Date"
    return df


def load(ticker: str, period: str = "7y", refresh: bool = False) -> pd.DataFrame:
    """Cached daily bars for one ticker. Delete .cache/ or pass refresh=True
    to re-download (e.g. after a split re-adjusts history)."""
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    path = CACHE_DIR / f"{ticker}_{period}.csv"
    if path.exists() and not refresh:
        return pd.read_csv(path, index_col="Date", parse_dates=True)
    df = _download(ticker, period)
    df.to_csv(path)
    return df


def load_many(tickers: List[str], period: str = "7y", refresh: bool = False) -> Dict[str, pd.DataFrame]:
    return {t: load(t, period, refresh) for t in tickers}
