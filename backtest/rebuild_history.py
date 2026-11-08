"""Rebuild the StockBot `history` sheet with the v2 engine.

    # preview only (default) — writes backtest/results/history_rebuild_preview.csv
    docker run ... -v /path/credentials.json:/creds.json \
        -e GOOGLE_CREDENTIALS_FILE=/creds.json -e SHEET_ID=... \
        stock-backtest python -m backtest.rebuild_history
    # write: copies `history` to a backup tab first, then replaces it
    ... python -m backtest.rebuild_history --apply

One row per ticker per real US trading day, from the first to the last
session that ticker was logged — the weekend/holiday/stale duplicate rows the
old StockBot wrote are dropped, and every row is recomputed by
api.stock_analysis.recommend_v2_frame so the whole history comes from one
engine. Columns A-G keep their layout and formats (read by position by the
AI worker and StockSignalDashboard); H-N are the same extra fields StockBot
now appends (see StockBot bot.py).

Price (G): the sheet's original value for that session where it exists and
agrees with Yahoo's close within --price-tolerance; otherwise Yahoo's close
(a mismatch means the original row carried stale data from another day).
"""
from __future__ import annotations

import argparse
import os
from datetime import datetime
from zoneinfo import ZoneInfo

import pandas as pd

from api.stock_analysis import ENGINE_VERSION, calculate_indicators_v2, recommend_v2_frame

from . import data

COLUMNS = ["session", "ticker", "signal", "strength", "confidence", "trend", "price",
           "engine", "regime", "rsi", "plus_di", "minus_di", "sma200", "generated_at"]


def _worksheet():
    import gspread
    from google.oauth2.service_account import Credentials
    creds = Credentials.from_service_account_file(
        os.environ["GOOGLE_CREDENTIALS_FILE"],
        scopes=["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"],
    )
    sh = gspread.authorize(creds).open_by_key(os.environ["SHEET_ID"])
    return sh, sh.worksheet(os.environ.get("SHEET_NAME", "history"))


def load_existing(ws) -> pd.DataFrame:
    rows = [r[:7] for r in ws.get_all_values() if len(r) >= 7 and len(r[0]) >= 10]
    df = pd.DataFrame(rows, columns=["stamp", "ticker", "signal", "strength", "confidence", "trend", "price"])
    df["stamp"] = pd.to_datetime(df.stamp.str[:10])
    # Rows were stamped at 08:00 Bangkok, after the previous US close.
    df["session"] = df.stamp - pd.offsets.BDay(1)
    df["price_num"] = pd.to_numeric(df.price.str.replace(r"[$,]", "", regex=True), errors="coerce")
    return df


def rebuild(existing: pd.DataFrame, tolerance: float) -> tuple[pd.DataFrame, dict]:
    generated_at = datetime.now(ZoneInfo("Asia/Bangkok")).strftime("%Y-%m-%d %H:%M:%S")
    out, stats = [], {"price_from_sheet": 0, "price_from_yahoo_missing": 0, "price_from_yahoo_mismatch": 0}
    for ticker, g in existing.groupby("ticker"):
        first, last = g.session.min(), g.session.max()
        px = data.load(ticker, refresh=False)
        if px.index[-1] < last:
            px = data.load(ticker, refresh=True)
        ind = calculate_indicators_v2(px)
        rec = recommend_v2_frame(ind)
        # first stamp for a session is the run right after that close
        orig = g.sort_values("stamp").drop_duplicates("session", keep="first").set_index("session").price_num
        sessions = ind.index[(ind.index >= first) & (ind.index <= last)]
        for s in sessions:
            close = float(px.at[s, "Close"])
            o = orig.get(s)
            if o is None or pd.isna(o):
                price, stats["price_from_yahoo_missing"] = close, stats["price_from_yahoo_missing"] + 1
            elif abs(o / close - 1) > tolerance:
                price, stats["price_from_yahoo_mismatch"] = close, stats["price_from_yahoo_mismatch"] + 1
            else:
                price, stats["price_from_sheet"] = float(o), stats["price_from_sheet"] + 1
            row = ind.loc[s]
            if s in rec.index:
                r = rec.loc[s]
                fields = [r.signal, r.strength, float(r.confidence), f"{r.trend_label} (ADX: {r.adx})",
                          r.regime, round(float(row.RSI), 2), round(float(row.Plus_DI), 2),
                          round(float(row.Minus_DI), 2), round(float(row.SMA200), 2)]
            else:  # under 200 bars of history: same fallback as the live endpoint
                fields = ["HOLD", "Insufficient", 0.0, "Weak (ADX: 0)", "", "", "", "", ""]
            out.append([s.strftime("%Y-%m-%d"), ticker, *fields[:4], f"${price:.2f}",
                        ENGINE_VERSION, *fields[4:], generated_at])
    df = pd.DataFrame(out, columns=COLUMNS).sort_values(["session", "ticker"], kind="stable")
    return df, stats


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true", help="back up and overwrite the sheet")
    ap.add_argument("--price-tolerance", type=float, default=0.015)
    ap.add_argument("--out", default="backtest/results/history_rebuild_preview.csv")
    a = ap.parse_args()

    sh, ws = _worksheet()
    existing = load_existing(ws)
    new, stats = rebuild(existing, a.price_tolerance)
    new.to_csv(a.out, index=False)

    print(f"existing rows: {len(existing)}  ->  rebuilt rows: {len(new)}")
    print(f"tickers: {existing.ticker.nunique()}  sessions: {new.session.min()}..{new.session.max()}")
    print(f"price source: {stats}")
    print(f"signal mix: {new.signal.value_counts().to_dict()}")
    per = pd.DataFrame({"before": existing.groupby("ticker").size(), "after": new.groupby("ticker").size()})
    print(per.to_string())
    print(f"preview written to {a.out}")

    if not a.apply:
        print("dry run — sheet not modified (pass --apply to write)")
        return
    backup = f"history_backup_{datetime.now(ZoneInfo('Asia/Bangkok')):%Y%m%d_%H%M}"
    sh.duplicate_sheet(ws.id, new_sheet_name=backup)
    print(f"backed up to tab '{backup}'")
    ws.clear()
    if len(new) > ws.row_count:
        ws.add_rows(len(new) - ws.row_count)
    ws.update(new.values.tolist(), "A1", value_input_option="USER_ENTERED")
    print(f"wrote {len(new)} rows to '{ws.title}'")


if __name__ == "__main__":
    main()
