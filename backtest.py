from __future__ import annotations

import csv
import json
import os
import time
from dataclasses import dataclass, asdict
from datetime import datetime, timezone, timedelta
from typing import Dict, List, Tuple
from zoneinfo import ZoneInfo

import ccxt
import pandas as pd

from config import settings
from logger import log
from bias_engine import weekly_bias, four_h_spm_allows
from dls_engine import detect_dls_signals
from ict_engine import detect_signal as detect_ict_signal

VERSION = "master-ict-dls-bot-v2-backtest"

TF_MS = {
    "15m": 15 * 60 * 1000,
    "30m": 30 * 60 * 1000,
    "1h": 60 * 60 * 1000,
    "2h": 2 * 60 * 60 * 1000,
    "4h": 4 * 60 * 60 * 1000,
    "1d": 24 * 60 * 60 * 1000,
    "1w": 7 * 24 * 60 * 60 * 1000,
}


@dataclass
class BacktestTrade:
    symbol: str
    strategy: str
    side: str
    timeframe: str
    entry_time: str
    entry: float
    stop: float
    target: float
    break_even_price: float
    risk_per_unit: float
    risk_usdt: float
    signal_id: str
    reason: str
    break_even_moved: bool = False
    bars_open: int = 0


class HistoricalClient:
    def __init__(self):
        self.exchange = ccxt.mexc({"enableRateLimit": True, "options": {"defaultType": "swap"}})
        self.exchange.load_markets()

    def fetch_history(self, symbol: str, timeframe: str, since_ms: int, end_ms: int, limit: int = 1000) -> pd.DataFrame:
        rows = []
        since = since_ms
        failures = 0
        while since < end_ms:
            try:
                batch = self.exchange.fetch_ohlcv(symbol, timeframe=timeframe, since=since, limit=limit)
                failures = 0
            except Exception as exc:
                msg = str(exc)
                if "Requests are too frequent" in msg or '"code":510' in msg:
                    log.warning("Rate limited fetching %s %s, sleeping %ss", symbol, timeframe, settings.rate_limit_backoff_seconds)
                    time.sleep(settings.rate_limit_backoff_seconds)
                    continue
                failures += 1
                if failures > 3:
                    raise
                log.warning("Fetch failed %s %s: %s", symbol, timeframe, exc)
                time.sleep(3)
                continue

            if not batch:
                break
            rows.extend(batch)
            last = batch[-1][0]
            next_since = last + TF_MS[timeframe]
            if next_since <= since:
                break
            since = next_since
            if len(batch) < 2:
                break
            time.sleep(float(settings.request_delay_seconds))

        if not rows:
            return pd.DataFrame(columns=["timestamp", "open", "high", "low", "close", "volume", "datetime"])
        df = pd.DataFrame(rows, columns=["timestamp", "open", "high", "low", "close", "volume"])
        df = df.drop_duplicates("timestamp").sort_values("timestamp")
        df = df[df["timestamp"] < end_ms].copy()
        df["datetime"] = pd.to_datetime(df["timestamp"], unit="ms", utc=True)
        for col in ["open", "high", "low", "close", "volume"]:
            df[col] = df[col].astype(float)
        return df.reset_index(drop=True)


def aggregate(df: pd.DataFrame, rule: str, limit: int | None = None) -> pd.DataFrame:
    if df.empty:
        return df.copy()
    x = df.copy().set_index("datetime")
    agg = x.resample(rule, label="left", closed="left").agg({
        "open": "first",
        "high": "max",
        "low": "min",
        "close": "last",
        "volume": "sum",
        "timestamp": "first",
    }).dropna().reset_index()
    out = agg[["timestamp", "open", "high", "low", "close", "volume", "datetime"]]
    if limit:
        out = out.tail(limit)
    return out.reset_index(drop=True)


def closed_until(df: pd.DataFrame, timeframe: str, now_ms: int, limit: int) -> pd.DataFrame:
    if df.empty:
        return df.copy()
    dur = TF_MS[timeframe]
    x = df[df["timestamp"] + dur <= now_ms].copy()
    return x.tail(limit).reset_index(drop=True)


def in_new_york_session(ts_utc: pd.Timestamp) -> bool:
    ny = ts_utc.tz_convert("America/New_York")
    start = ny.replace(hour=settings.ny_start_hour, minute=0, second=0, microsecond=0)
    end = ny.replace(hour=settings.ny_end_hour, minute=settings.ny_end_minute, second=0, microsecond=0)
    return start <= ny <= end


def normalize_ict_signal(sig):
    sig.strategy = "ICT"
    sig.timeframe = "15m"
    risk = abs(sig.entry - sig.stop)
    sig.risk_per_unit = risk
    sig.break_even_price = sig.entry + risk * settings.break_even_r if sig.side == "buy" else sig.entry - risk * settings.break_even_r
    sig.signal_id = f"{sig.symbol}|ICT|{sig.side}|{pd.Timestamp(sig.signal_time).isoformat()}|{round(sig.entry,8)}|{round(sig.stop,8)}"
    return sig


def build_signals_for_symbol(symbol: str, dfs: Dict[str, pd.DataFrame]):
    bias, bias_reason = weekly_bias(dfs["1w"], dfs["1d"])
    if bias not in ("buy", "sell"):
        return []
    ok4h, reason4h = four_h_spm_allows(dfs["4h"], bias)
    if not ok4h:
        return []

    signals = []
    if settings.enable_dls:
        for s in detect_dls_signals(symbol, dfs):
            if s.side == bias:
                s.reason = f"{bias_reason}; {reason4h}; {s.reason}"
                signals.append(s)

    if settings.enable_ict:
        try:
            ict, _ = detect_ict_signal(symbol, dfs["1d"], dfs["4h"], dfs["1h"], dfs["15m"])
            if ict:
                ict = normalize_ict_signal(ict)
                if ict.side == bias:
                    ict.reason = f"{bias_reason}; {reason4h}; {ict.reason}"
                    signals.append(ict)
        except Exception:
            pass
    return signals


def manage_trade_on_bar(trade: BacktestTrade, bar) -> Tuple[bool, str, float, float]:
    """Return (closed, result, exit_price, r). Conservative assumption: stop is checked before target if both occur in same candle."""
    high = float(bar.high)
    low = float(bar.low)
    trade.bars_open += 1

    if trade.side == "buy":
        if low <= trade.stop:
            r = (trade.stop - trade.entry) / trade.risk_per_unit
            return True, "LOSS" if r < 0 else "BREAKEVEN", trade.stop, r
        if high >= trade.target:
            return True, "WIN", trade.target, (trade.target - trade.entry) / trade.risk_per_unit
        if not trade.break_even_moved and high >= trade.break_even_price:
            trade.stop = trade.entry
            trade.break_even_moved = True
    else:
        if high >= trade.stop:
            r = (trade.entry - trade.stop) / trade.risk_per_unit
            return True, "LOSS" if r < 0 else "BREAKEVEN", trade.stop, r
        if low <= trade.target:
            return True, "WIN", trade.target, (trade.entry - trade.target) / trade.risk_per_unit
        if not trade.break_even_moved and low <= trade.break_even_price:
            trade.stop = trade.entry
            trade.break_even_moved = True

    if trade.bars_open >= int(settings.backtest_max_hold_bars_15m):
        exit_price = float(bar.close)
        if trade.side == "buy":
            r = (exit_price - trade.entry) / trade.risk_per_unit
        else:
            r = (trade.entry - exit_price) / trade.risk_per_unit
        result = "WIN" if r > 0 else "LOSS" if r < 0 else "BREAKEVEN"
        return True, f"TIME_EXIT_{result}", exit_price, r

    return False, "", 0.0, 0.0


def run_backtest():
    os.makedirs("logs", exist_ok=True)
    client = HistoricalClient()
    symbols = [s for s in settings.backtest_symbol_list if s in client.exchange.markets]
    if not symbols:
        raise RuntimeError("No valid backtest symbols. Check BACKTEST_SYMBOLS.")

    end_dt = datetime.now(timezone.utc)
    start_dt = end_dt - timedelta(days=int(settings.backtest_days))
    log.info("Starting backtest %s | days=%s symbols=%s", VERSION, settings.backtest_days, symbols)
    log.info("Backtest period UTC: %s -> %s", start_dt.isoformat(), end_dt.isoformat())

    end_ms = int(end_dt.timestamp() * 1000)
    starts = {
        "1w": int((start_dt - timedelta(days=400)).timestamp() * 1000),
        "1d": int((start_dt - timedelta(days=220)).timestamp() * 1000),
        "4h": int((start_dt - timedelta(days=120)).timestamp() * 1000),
        "1h": int((start_dt - timedelta(days=70)).timestamp() * 1000),
        "15m": int((start_dt - timedelta(days=45)).timestamp() * 1000),
    }

    data: Dict[str, Dict[str, pd.DataFrame]] = {}
    for symbol in symbols:
        log.info("Fetching history for %s", symbol)
        data[symbol] = {
            "1w": client.fetch_history(symbol, "1w", starts["1w"], end_ms, limit=1000),
            "1d": client.fetch_history(symbol, "1d", starts["1d"], end_ms, limit=1000),
            "4h": client.fetch_history(symbol, "4h", starts["4h"], end_ms, limit=1000),
            "1h": client.fetch_history(symbol, "1h", starts["1h"], end_ms, limit=1000),
            "15m": client.fetch_history(symbol, "15m", starts["15m"], end_ms, limit=1000),
        }
        log.info("Fetched %s rows: 1w=%s 1d=%s 4h=%s 1h=%s 15m=%s", symbol, len(data[symbol]["1w"]), len(data[symbol]["1d"]), len(data[symbol]["4h"]), len(data[symbol]["1h"]), len(data[symbol]["15m"]))

    series_list = []
    for symbol in symbols:
        m15 = data[symbol]["15m"]
        if not m15.empty:
            series_list.append(m15.loc[m15["datetime"] >= pd.Timestamp(start_dt), "datetime"])
    if series_list:
        all_times = sorted(pd.concat(series_list).drop_duplicates().tolist())
    else:
        all_times = []

    balance = float(settings.backtest_start_balance)
    open_trades: List[BacktestTrade] = []
    seen_signals = set()
    closed_rows = []

    for ts in all_times:
        now_ms = int(pd.Timestamp(ts).timestamp() * 1000)

        # Manage all open trades on each 15m bar.
        for trade in list(open_trades):
            m15 = data[trade.symbol]["15m"]
            bar_rows = m15[m15["datetime"] == ts]
            if bar_rows.empty:
                continue
            closed, result, exit_price, r = manage_trade_on_bar(trade, bar_rows.iloc[0])
            if closed:
                pnl = r * trade.risk_usdt
                balance += pnl
                row = {
                    **asdict(trade),
                    "exit_time": pd.Timestamp(ts).isoformat(),
                    "exit_price": exit_price,
                    "result": result,
                    "r": r,
                    "pnl_usdt": pnl,
                    "balance_after": balance,
                }
                closed_rows.append(row)
                open_trades.remove(trade)

        if settings.backtest_use_newyork_session and not in_new_york_session(pd.Timestamp(ts)):
            continue
        if len(open_trades) >= int(settings.backtest_max_open_positions):
            continue

        candidates = []
        for symbol in symbols:
            if any(t.symbol == symbol for t in open_trades):
                continue
            raw = data[symbol]
            dfs = {
                "1w": closed_until(raw["1w"], "1w", now_ms, 80),
                "1d": closed_until(raw["1d"], "1d", now_ms, 160),
                "4h": closed_until(raw["4h"], "4h", now_ms, 220),
                "1h": closed_until(raw["1h"], "1h", now_ms, 260),
                "15m": closed_until(raw["15m"], "15m", now_ms, 300),
            }
            dfs["2h"] = aggregate(dfs["1h"], "2h", 160)
            dfs["30m"] = aggregate(dfs["15m"], "30min", 220)
            if min(len(dfs["1w"]), len(dfs["1d"]), len(dfs["4h"]), len(dfs["1h"]), len(dfs["15m"])) < 10:
                continue
            for sig in build_signals_for_symbol(symbol, dfs):
                # Only accept signals that confirm on the current backtest bar. This prevents historical old setups being opened late.
                sig_time = pd.Timestamp(sig.signal_time)
                if sig_time.tzinfo is None:
                    sig_time = sig_time.tz_localize("UTC")
                if sig_time != pd.Timestamp(ts):
                    continue
                if sig.signal_id in seen_signals:
                    continue
                candidates.append(sig)

        candidates.sort(key=lambda s: (s.score, 1 if str(s.strategy).startswith("DLS") else 0), reverse=True)
        slots = int(settings.backtest_max_open_positions) - len(open_trades)
        take = min(slots, int(settings.backtest_max_new_trades_per_bar), len(candidates))
        for sig in candidates[:take]:
            risk_unit = abs(float(sig.entry) - float(sig.stop))
            if risk_unit <= 0:
                continue
            risk_usdt = balance * float(settings.backtest_risk_per_trade)
            open_trades.append(BacktestTrade(
                symbol=sig.symbol,
                strategy=sig.strategy,
                side=sig.side,
                timeframe=sig.timeframe,
                entry_time=pd.Timestamp(ts).isoformat(),
                entry=float(sig.entry),
                stop=float(sig.stop),
                target=float(sig.target),
                break_even_price=float(sig.break_even_price),
                risk_per_unit=risk_unit,
                risk_usdt=risk_usdt,
                signal_id=sig.signal_id,
                reason=sig.reason,
            ))
            seen_signals.add(sig.signal_id)

    # Mark remaining open trades as OPEN at the final price.
    for trade in list(open_trades):
        m15 = data[trade.symbol]["15m"]
        last_bar = m15.iloc[-1]
        exit_price = float(last_bar.close)
        r = (exit_price - trade.entry) / trade.risk_per_unit if trade.side == "buy" else (trade.entry - exit_price) / trade.risk_per_unit
        row = {
            **asdict(trade),
            "exit_time": pd.Timestamp(last_bar.datetime).isoformat(),
            "exit_price": exit_price,
            "result": "OPEN",
            "r": r,
            "pnl_usdt": r * trade.risk_usdt,
            "balance_after": balance,
        }
        closed_rows.append(row)

    with open(settings.backtest_result_file, "w", newline="") as f:
        if closed_rows:
            writer = csv.DictWriter(f, fieldnames=list(closed_rows[0].keys()))
            writer.writeheader()
            writer.writerows(closed_rows)
        else:
            writer = csv.writer(f)
            writer.writerow(["no_trades_found"])

    wins = sum(1 for r in closed_rows if str(r["result"]).startswith("WIN"))
    losses = sum(1 for r in closed_rows if str(r["result"]).startswith("LOSS"))
    breakeven = sum(1 for r in closed_rows if str(r["result"]).startswith("BREAKEVEN"))
    open_count = sum(1 for r in closed_rows if r["result"] == "OPEN")
    closed_count = wins + losses + breakeven
    net_r = sum(float(r["r"]) for r in closed_rows if r["result"] != "OPEN")
    summary = {
        "version": VERSION,
        "period_start_utc": start_dt.isoformat(),
        "period_end_utc": end_dt.isoformat(),
        "symbols": symbols,
        "total_trades_found": len(closed_rows),
        "closed_trades": closed_count,
        "wins": wins,
        "losses": losses,
        "breakeven": breakeven,
        "open_at_end": open_count,
        "win_rate_closed_percent": round((wins / closed_count) * 100, 2) if closed_count else 0.0,
        "net_r_closed": round(net_r, 4),
        "start_balance": settings.backtest_start_balance,
        "end_balance_closed_only": round(balance, 4),
        "risk_per_trade": settings.backtest_risk_per_trade,
        "results_file": settings.backtest_result_file,
    }
    with open(settings.backtest_summary_file, "w") as f:
        json.dump(summary, f, indent=2)

    log.warning("BACKTEST COMPLETE: trades=%s closed=%s wins=%s losses=%s BE=%s open=%s winrate=%.2f%% netR=%.2f", summary["total_trades_found"], closed_count, wins, losses, breakeven, open_count, summary["win_rate_closed_percent"], summary["net_r_closed"])
    print(json.dumps(summary, indent=2))
    return summary


if __name__ == "__main__":
    run_backtest()
