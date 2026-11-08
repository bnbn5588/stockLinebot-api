"""Checks the harness's `current` replay against what production actually
logged to the StockBot history sheet — if they agree, backtest results for
`current` describe the live system, not an approximation of it.

    SHEET_ID=... python -m backtest.parity

Sheet rows are stamped with the Bangkok wall-clock time StockBot ran
(08:00 BKK), which is after the previous US session's close, so a row dated
D reflects the US session on the last weekday before D. Rows whose session
was already logged (weekend/Monday re-runs) are dropped as duplicates.
"""
from __future__ import annotations

import io
import os
import sys

import pandas as pd
import requests

from .run import _signals


def fetch_history(sheet_id: str, sheet_name: str = "history") -> pd.DataFrame:
    url = f"https://docs.google.com/spreadsheets/d/{sheet_id}/gviz/tq?tqx=out:csv&sheet={sheet_name}"
    res = requests.get(url, timeout=30)
    res.raise_for_status()
    raw = pd.read_csv(io.StringIO(res.text), header=None, dtype=str)
    df = pd.DataFrame({
        "stamp": pd.to_datetime(raw[0].str[:10], errors="coerce"),
        "ticker": raw[1].str.strip(),
        "signal": raw[2].str.strip(),
        "confidence": pd.to_numeric(raw[4], errors="coerce"),
    }).dropna(subset=["stamp", "ticker"])
    df = df[df.signal.isin(["BUY", "SELL", "HOLD"])]
    df["session"] = df.stamp - pd.offsets.BDay(1)
    return df.drop_duplicates(["ticker", "session"], keep="first")


def main():
    sheet_id = os.environ.get("SHEET_ID") or sys.exit("set SHEET_ID")
    hist = fetch_history(sheet_id, os.environ.get("SHEET_NAME", "history"))
    rows = []
    for t, g in hist.groupby("ticker"):
        sig = _signals(os.environ.get("ENGINE", "current"), t, "7y", os.environ.get("SINCE", "2021-01-01"))
        m = g.merge(sig[["signal", "confidence"]], left_on="session", right_index=True,
                    how="inner", suffixes=("_sheet", "_replay"))
        m["ticker"] = t
        rows.append(m)
    m = pd.concat(rows)
    m["match"] = m.signal_sheet == m.signal_replay
    m["conf_diff"] = (m.confidence_sheet - m.confidence_replay).abs()
    print(f"compared {len(m)} ticker-sessions "
          f"({m.session.min().date()}..{m.session.max().date()})")
    print(f"signal match: {m.match.mean() * 100:.1f}%   "
          f"confidence |diff| <= 1pt: {(m.conf_diff <= 1).mean() * 100:.1f}%")
    # API logic changed on 2026-04-16 ("enhance the analysis") and its stale-
    # data fix landed 2026-09-04 — match rate per era shows which history
    # rows were produced by today's logic.
    eras = pd.cut(m.session, [pd.Timestamp("2000-01-01"), pd.Timestamp("2026-04-16"),
                              pd.Timestamp("2026-09-04"), pd.Timestamp("2100-01-01")],
                  labels=["before 04-16", "04-16..09-04", "after 09-04"], right=False)
    print("\nmatch % by API era:")
    print(m.groupby(eras, observed=True).match.agg(["size", "mean"]).assign(mean=lambda d: d["mean"] * 100).to_string())
    print("\nper ticker match %:")
    print(m.groupby("ticker").match.mean().mul(100).round(1).to_string())
    bad = m[~m.match]
    if len(bad):
        print("\nsample mismatches:")
        print(bad[["ticker", "session", "signal_sheet", "confidence_sheet",
                   "signal_replay", "confidence_replay"]].head(15).to_string(index=False))


if __name__ == "__main__":
    main()
