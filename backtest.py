import os
import json
import time
import logging
from dataclasses import dataclass, asdict
from datetime import datetime, timezone, timedelta
from typing import Optional, Literal

import pandas as pd

from config import settings
from mexc_client import MexcClient

logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")
log = logging.getLogger("filtered-backtest")

VERSION = "backtest-weekly-4h-spm-filter-v2-self-contained"
Side = Literal["buy", "sell"]

BACKTEST_DAYS = int(os.getenv("BACKTEST_DAYS", "30"))
START_BALANCE = float(os.getenv("BACKTEST_START_BALANCE", "1000"))
RISK_PER_TRADE = float(os.getenv("RISK_PER_TRADE", "0.02"))
RR_TARGET = float(os.getenv("RR_TARGET", "2.0"))
BREAK_EVEN_R = float(os.getenv("BREAK_EVEN_R", "0.82"))
USE_WEEKLY_FILTER = os.getenv("USE_WEEKLY_FILTER", "true").lower() == "true"
USE_4H_SPM_FILTER = os.getenv("USE_4H_SPM_FILTER", "true").lower() == "true"

os.makedirs("logs", exist_ok=True)


@dataclass
class SimpleSignal:
    side: Side
    entry: float
    stop_loss: float
    timeframe: str


@dataclass
class BTTrade:
    symbol: str
    strategy: str
    side: Side
    entry_time: str
    entry: float
    stop: float
    target: float
    be_price: float
    weekly_bias: str
    h4_spm: str
    allowed: bool
    reject_reason: str = ""
    exit_time: str = ""
    exit_price: float = 0.0
    result: str = "OPEN"
    r: float = 0.0


def normalize_df(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    if "datetime" not in df.columns:
        df["datetime"] = pd.to_datetime(df["timestamp"], unit="ms", utc=True)
    else:
        df["datetime"] = pd.to_datetime(df["datetime"], utc=True)
    return df.sort_values("datetime").drop_duplicates("datetime").reset_index(drop=True)


def body_high(row) -> float:
    return max(float(row["open"]), float(row["close"]))


def body_low(row) -> float:
    return min(float(row["open"]), float(row["close"]))


def is_inside_candle(df: pd.DataFrame, i: int) -> bool:
    if i <= 0:
        return False
    c = df.iloc[i]
    p = df.iloc[i - 1]
    return float(c["high"]) <= float(p["high"]) and float(c["low"]) >= float(p["low"])


def weekly_bias_at(weekly: pd.DataFrame, t) -> str:
    w = weekly[weekly["datetime"] < t].copy()
    if len(w) < 3:
        return "neutral"
    last_week = w.iloc[-1]
    prev_week = w.iloc[-2]
    if float(last_week["close"]) > float(prev_week["high"]):
        return "buy"
    if float(last_week["close"]) < float(prev_week["low"]):
        return "sell"
    return "neutral"


@dataclass
class SPM:
    side: Side
    confirmed_time: object
    c1_time: object
    c2_time: object
    c1_level: float
    c2_extreme: float


def find_valid_bull_c1(df: pd.DataFrame, c2_i: int, max_back: int = 50) -> Optional[int]:
    c2 = df.iloc[c2_i]
    start = max(1, c2_i - max_back)
    for i in range(c2_i - 1, start - 1, -1):
        c1 = df.iloc[i]
        if is_inside_candle(df, i):
            continue
        if float(c2["low"]) <= float(c1["low"]):
            continue
        if float(c2["close"]) < body_low(c1):
            continue
        return i
    return None


def find_valid_bear_c1(df: pd.DataFrame, c2_i: int, max_back: int = 50) -> Optional[int]:
    c2 = df.iloc[c2_i]
    start = max(1, c2_i - max_back)
    for i in range(c2_i - 1, start - 1, -1):
        c1 = df.iloc[i]
        if is_inside_candle(df, i):
            continue
        if float(c2["high"]) >= float(c1["high"]):
            continue
        if float(c2["close"]) > body_high(c1):
            continue
        return i
    return None


def detect_last_spm(df: pd.DataFrame, t, lookback: int = 160) -> Optional[SPM]:
    data = df[df["datetime"] < t].copy().reset_index(drop=True)
    if len(data) < 30:
        return None

    start = max(5, len(data) - lookback)
    spms = []

    for c2_i in range(start, len(data) - 1):
        left = max(0, c2_i - 20)
        right = min(len(data), c2_i + 21)
        window = data.iloc[left:right]

        # Bullish SPM: Candle 2 is lowest candle, confirm body close above Candle 1 high
        if float(data.iloc[c2_i]["low"]) == float(window["low"].min()):
            c1_i = find_valid_bull_c1(data, c2_i)
            if c1_i is not None:
                c1 = data.iloc[c1_i]
                level = float(c1["high"])
                after = data.iloc[c2_i + 1:]
                confirms = after[after["close"] > level]
                if not confirms.empty:
                    conf = confirms.iloc[0]
                    spms.append(SPM("buy", conf["datetime"], c1["datetime"], data.iloc[c2_i]["datetime"], level, float(data.iloc[c2_i]["low"])))

        # Bearish SPM: Candle 2 is highest candle, confirm body close below Candle 1 low
        if float(data.iloc[c2_i]["high"]) == float(window["high"].max()):
            c1_i = find_valid_bear_c1(data, c2_i)
            if c1_i is not None:
                c1 = data.iloc[c1_i]
                level = float(c1["low"])
                after = data.iloc[c2_i + 1:]
                confirms = after[after["close"] < level]
                if not confirms.empty:
                    conf = confirms.iloc[0]
                    spms.append(SPM("sell", conf["datetime"], c1["datetime"], data.iloc[c2_i]["datetime"], level, float(data.iloc[c2_i]["high"])))

    if not spms:
        return None
    spms.sort(key=lambda x: x.confirmed_time)
    return spms[-1]


def h4_spm_side_at(h4: pd.DataFrame, t) -> str:
    spm = detect_last_spm(h4, t)
    return spm.side if spm else "none"


def passes_filters(side: str, weekly_bias: str, h4_spm: str):
    reasons = []
    if USE_WEEKLY_FILTER and weekly_bias != "neutral" and weekly_bias != side:
        reasons.append(f"weekly mismatch weekly={weekly_bias} trade={side}")
    if USE_4H_SPM_FILTER:
        if h4_spm == "none":
            reasons.append("no 4H SPM")
        elif h4_spm != side:
            reasons.append(f"4H SPM mismatch h4={h4_spm} trade={side}")
    return len(reasons) == 0, "; ".join(reasons)


def make_trade(symbol, strategy, side, entry, stop, t, weekly_bias, h4_spm):
    if side == "buy":
        risk = entry - stop
        target = entry + risk * RR_TARGET
        be = entry + risk * BREAK_EVEN_R
    else:
        risk = stop - entry
        target = entry - risk * RR_TARGET
        be = entry - risk * BREAK_EVEN_R
    if risk <= 0:
        return None
    allowed, reject_reason = passes_filters(side, weekly_bias, h4_spm)
    return BTTrade(symbol, strategy, side, str(t), float(entry), float(stop), float(target), float(be), weekly_bias, h4_spm, allowed, reject_reason)


def detect_dls_type1(d: pd.DataFrame, timeframe: str) -> Optional[SimpleSignal]:
    if len(d) < 3:
        return None

    c1 = d.iloc[-3]
    c2 = d.iloc[-2]
    c3 = d.iloc[-1]

    c1_high = float(c1["high"])
    c1_low = float(c1["low"])

    c2_high = float(c2["high"])
    c2_low = float(c2["low"])
    c2_close = float(c2["close"])

    c3_high = float(c3["high"])
    c3_low = float(c3["low"])
    c3_close = float(c3["close"])

    c2_body_top = body_high(c2)
    c2_body_bottom = body_low(c2)

    # BUY Type 1
    buy_ok = (
        c2_high > c1_high and
        c2_close < c1_high and
        c3_low < c1_low and
        c3_close > c2_body_top
    )
    if buy_ok and c3_close > c3_low:
        return SimpleSignal("buy", c3_close, c3_low, timeframe)

    # SELL Type 1
    sell_ok = (
        c2_low < c1_low and
        c2_close > c1_low and
        c3_high > c1_high and
        c3_close < c2_body_bottom
    )
    if sell_ok and c3_high > c3_close:
        return SimpleSignal("sell", c3_close, c3_high, timeframe)

    return None


def detect_dls_candidates(symbol: str, df: pd.DataFrame, timeframe: str, t, weekly_bias, h4_spm):
    out = []
    d = df[df["datetime"] <= t].copy().reset_index(drop=True)
    if len(d) < 3:
        return out

    sig1 = detect_dls_type1(d, timeframe)
    if sig1:
        tr = make_trade(symbol, "DLS_TYPE1", sig1.side, sig1.entry, sig1.stop_loss, t, weekly_bias, h4_spm)
        if tr:
            out.append(tr)
        return out

    c1, c2, c3 = d.iloc[-3], d.iloc[-2], d.iloc[-1]
    c1_high, c1_low = float(c1["high"]), float(c1["low"])
    c2_high, c2_low = float(c2["high"]), float(c2["low"])
    c2_close, c2_open = float(c2["close"]), float(c2["open"])
    c3_high, c3_low, c3_close = float(c3["high"]), float(c3["low"]), float(c3["close"])

    # DLS Type 2: DLS happens but C3 does not close beyond C2 open
    buy2 = c2_high > c1_high and c2_close < c1_high and c3_low < c1_low and c3_close <= c2_open
    if buy2:
        tr = make_trade(symbol, "DLS_TYPE2", "buy", c3_close, c3_low, t, weekly_bias, h4_spm)
        if tr:
            out.append(tr)

    sell2 = c2_low < c1_low and c2_close > c1_low and c3_high > c1_high and c3_close >= c2_open
    if sell2:
        tr = make_trade(symbol, "DLS_TYPE2", "sell", c3_close, c3_high, t, weekly_bias, h4_spm)
        if tr:
            out.append(tr)

    return out


def simulate_trade(trade: BTTrade, m15: pd.DataFrame) -> BTTrade:
    entry_time = pd.Timestamp(trade.entry_time)
    future = m15[m15["datetime"] > entry_time].copy()
    moved_be = False

    for _, bar in future.iterrows():
        high = float(bar["high"])
        low = float(bar["low"])
        dt = str(bar["datetime"])

        if trade.side == "buy":
            if not moved_be and high >= trade.be_price:
                moved_be = True
            active_stop = trade.entry if moved_be else trade.stop

            if low <= active_stop:
                trade.exit_time = dt
                trade.exit_price = active_stop
                trade.result = "BREAKEVEN" if moved_be else "LOSS"
                trade.r = 0.0 if moved_be else -1.0
                return trade

            if high >= trade.target:
                trade.exit_time = dt
                trade.exit_price = trade.target
                trade.result = "WIN"
                trade.r = RR_TARGET
                return trade

        else:
            if not moved_be and low <= trade.be_price:
                moved_be = True
            active_stop = trade.entry if moved_be else trade.stop

            if high >= active_stop:
                trade.exit_time = dt
                trade.exit_price = active_stop
                trade.result = "BREAKEVEN" if moved_be else "LOSS"
                trade.r = 0.0 if moved_be else -1.0
                return trade

            if low <= trade.target:
                trade.exit_time = dt
                trade.exit_price = trade.target
                trade.result = "WIN"
                trade.r = RR_TARGET
                return trade

    return trade


def fetch_df(client, symbol, timeframe, limit):
    return normalize_df(client.fetch_ohlcv_df(symbol, timeframe, limit))


def main():
    client = MexcClient()
    symbols = list(settings.symbols)
    now = datetime.now(timezone.utc)
    start = now - timedelta(days=BACKTEST_DAYS)

    log.info("Starting filtered backtest %s | days=%s RR=%s weekly=%s h4spm=%s",
             VERSION, BACKTEST_DAYS, RR_TARGET, USE_WEEKLY_FILTER, USE_4H_SPM_FILTER)

    history = {}
    for symbol in symbols:
        log.info("Fetching %s", symbol)
        history[symbol] = {
            "1w": fetch_df(client, symbol, "1w", 80),
            "4h": fetch_df(client, symbol, "4h", 1000),
            "2h": fetch_df(client, symbol, "2h", 1000),
            "1h": fetch_df(client, symbol, "1h", 2500),
            "15m": fetch_df(client, symbol, "15m", 8000),
        }
        time.sleep(1)

    candidates = []

    for symbol, data in history.items():
        times = data["15m"][(data["15m"]["datetime"] >= pd.Timestamp(start)) & (data["15m"]["datetime"] <= pd.Timestamp(now))]["datetime"]

        for idx, t in enumerate(times):
            if idx % 500 == 0:
                log.info("Replay %s progress %s/%s candidates=%s", symbol, idx, len(times), len(candidates))

            # check 1H at the top of the hour
            if int(t.minute) == 0:
                wb = weekly_bias_at(data["1w"], t)
                h4s = h4_spm_side_at(data["4h"], t)
                candidates.extend(detect_dls_candidates(symbol, data["1h"], "1h", t, wb, h4s))

            # check 2H every 2 hours
            if int(t.minute) == 0 and int(t.hour) % 2 == 0:
                wb = weekly_bias_at(data["1w"], t)
                h4s = h4_spm_side_at(data["4h"], t)
                candidates.extend(detect_dls_candidates(symbol, data["2h"], "2h", t, wb, h4s))

    # dedupe
    unique = {}
    for tr in candidates:
        key = (tr.symbol, tr.strategy, tr.side, tr.entry_time, round(tr.entry, 8), round(tr.stop, 8))
        unique[key] = tr
    candidates = list(unique.values())

    allowed = [t for t in candidates if t.allowed]
    rejected = [t for t in candidates if not t.allowed]

    executed = [simulate_trade(t, history[t.symbol]["15m"]) for t in allowed]

    closed = [t for t in executed if t.result != "OPEN"]
    wins = [t for t in closed if t.result == "WIN"]
    losses = [t for t in closed if t.result == "LOSS"]
    bes = [t for t in closed if t.result == "BREAKEVEN"]

    balance = START_BALANCE
    for tr in closed:
        balance *= (1 + RISK_PER_TRADE * tr.r)

    rejected_weekly = [t for t in rejected if "weekly mismatch" in t.reject_reason]
    rejected_h4 = [t for t in rejected if "4H SPM" in t.reject_reason or "no 4H SPM" in t.reject_reason]

    breakdown = {}
    for strat in sorted(set(t.strategy for t in executed)):
        rows = [t for t in executed if t.strategy == strat]
        rows_closed = [t for t in rows if t.result != "OPEN"]
        breakdown[strat] = {
            "trades": len(rows),
            "closed": len(rows_closed),
            "wins": len([x for x in rows_closed if x.result == "WIN"]),
            "losses": len([x for x in rows_closed if x.result == "LOSS"]),
            "breakeven": len([x for x in rows_closed if x.result == "BREAKEVEN"]),
            "net_r": round(sum(x.r for x in rows_closed), 2),
        }

    summary = {
        "version": VERSION,
        "rr_target": RR_TARGET,
        "break_even_r": BREAK_EVEN_R,
        "period_days": BACKTEST_DAYS,
        "symbols": symbols,
        "total_candidates_before_filters": len(candidates),
        "rejected_total": len(rejected),
        "rejected_weekly": len(rejected_weekly),
        "rejected_4h_spm": len(rejected_h4),
        "executed_trades": len(executed),
        "closed_trades": len(closed),
        "wins": len(wins),
        "losses": len(losses),
        "breakeven": len(bes),
        "open_at_end": len([t for t in executed if t.result == "OPEN"]),
        "win_rate_closed_percent": round((len(wins) / len(closed) * 100) if closed else 0, 2),
        "net_r_closed": round(sum(t.r for t in closed), 2),
        "start_balance": START_BALANCE,
        "end_balance_closed_only": round(balance, 4),
        "use_weekly_filter": USE_WEEKLY_FILTER,
        "use_4h_spm_filter": USE_4H_SPM_FILTER,
        "strategy_breakdown": breakdown,
        "results_file": "logs/backtest_results_filtered.csv",
        "summary_file": "logs/backtest_summary_filtered.json",
    }

    rows = [asdict(t) for t in executed + rejected]
    pd.DataFrame(rows).to_csv("logs/backtest_results_filtered.csv", index=False)
    with open("logs/backtest_summary_filtered.json", "w") as f:
        json.dump(summary, f, indent=2)

    log.warning("FILTERED BACKTEST COMPLETE")
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
