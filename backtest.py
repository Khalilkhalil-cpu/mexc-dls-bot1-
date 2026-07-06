# Backtest helper.
# Runs the same DLS/SPM weekly+4H filter logic over recent history.
# For full detailed testing, use Railway command: python backtest.py

import json
import os
import time
from dataclasses import asdict
from datetime import datetime, timezone, timedelta

import pandas as pd

from bias_engine import final_bias, h4_spm_filter
from config import settings
from dls_engine import detect_dls
from ict_engine import detect_ict_signal
from logger import log
from mexc_client import MexcClient


def simulate(signal, m15):
    entry_t = pd.Timestamp(signal.signal_time, unit="ms", tz="UTC")
    future = m15[m15["datetime"] > entry_t].copy()
    moved_be = False

    result = {
        "symbol": signal.symbol,
        "strategy": signal.strategy,
        "side": signal.side,
        "timeframe": signal.timeframe,
        "entry_time": str(entry_t),
        "entry": signal.entry,
        "stop": signal.stop_loss,
        "target": signal.take_profit,
        "result": "OPEN",
        "r": 0.0,
        "exit_time": "",
        "exit_price": None,
    }

    for _, bar in future.iterrows():
        high, low = float(bar.high), float(bar.low)

        if signal.side == "buy":
            if not moved_be and high >= signal.break_even_price:
                moved_be = True
            active_stop = signal.entry if moved_be else signal.stop_loss
            if low <= active_stop:
                result["result"] = "BREAKEVEN" if moved_be else "LOSS"
                result["r"] = 0.0 if moved_be else -1.0
                result["exit_time"] = str(bar.datetime)
                result["exit_price"] = active_stop
                return result
            if high >= signal.take_profit:
                result["result"] = "WIN"
                result["r"] = settings.rr_target
                result["exit_time"] = str(bar.datetime)
                result["exit_price"] = signal.take_profit
                return result
        else:
            if not moved_be and low <= signal.break_even_price:
                moved_be = True
            active_stop = signal.entry if moved_be else signal.stop_loss
            if high >= active_stop:
                result["result"] = "BREAKEVEN" if moved_be else "LOSS"
                result["r"] = 0.0 if moved_be else -1.0
                result["exit_time"] = str(bar.datetime)
                result["exit_price"] = active_stop
                return result
            if low <= signal.take_profit:
                result["result"] = "WIN"
                result["r"] = settings.rr_target
                result["exit_time"] = str(bar.datetime)
                result["exit_price"] = signal.take_profit
                return result

    return result


def main():
    os.makedirs("logs", exist_ok=True)
    client = MexcClient()
    symbols = list(settings.backtest_symbol_list)
    now = datetime.now(timezone.utc)
    start = now - timedelta(days=settings.backtest_days)

    log.info("Starting backtest | days=%s risk=%s RR=%s symbols=%s", settings.backtest_days, settings.risk_per_trade, settings.rr_target, symbols)

    results = []
    rejected = 0

    for symbol in symbols:
        log.info("Fetching %s", symbol)
        df_1w = client.fetch_closed_df(symbol, "1w", 80)
        time.sleep(settings.request_delay_seconds)
        df_1d = client.fetch_closed_df(symbol, "1d", 260)
        time.sleep(settings.request_delay_seconds)
        df_4h = client.fetch_closed_df(symbol, "4h", 1000)
        time.sleep(settings.request_delay_seconds)
        df_1h = client.fetch_closed_df(symbol, "1h", 2500)
        time.sleep(settings.request_delay_seconds)
        df_2h = client.aggregate_2h_from_1h(df_1h, 1000)
        df_15m = client.fetch_closed_df(symbol, "15m", 8000)

        times = df_15m[(df_15m["datetime"] >= pd.Timestamp(start)) & (df_15m["datetime"] <= pd.Timestamp(now))]["datetime"]

        seen = set()
        for idx, t in enumerate(times):
            if idx % 500 == 0:
                log.info("Replay %s %s/%s results=%s rejected=%s", symbol, idx, len(times), len(results), rejected)

            if int(t.minute) != 0:
                continue

            hist_1w = df_1w[df_1w.datetime <= t]
            hist_1d = df_1d[df_1d.datetime <= t]
            hist_4h = df_4h[df_4h.datetime <= t]
            hist_1h = df_1h[df_1h.datetime <= t]
            hist_2h = df_2h[df_2h.datetime <= t]
            hist_15m = df_15m[df_15m.datetime <= t]

            if len(hist_1w) < 3 or len(hist_4h) < 30:
                continue

            bias = final_bias(hist_1w, hist_1d)
            if bias == "neutral":
                rejected += 1
                continue

            ok4h, reason4h = h4_spm_filter(hist_4h, bias)
            if not ok4h:
                rejected += 1
                continue

            candidates = []

            sig = detect_dls(symbol, hist_1h, "1h")
            if sig and sig.side == bias:
                candidates.append(sig)

            if int(t.hour) % 2 == 0:
                sig = detect_dls(symbol, hist_2h, "2h")
                if sig and sig.side == bias:
                    candidates.append(sig)

            sig = detect_ict_signal(symbol, bias, hist_4h, hist_1h, hist_15m)
            if sig:
                candidates.append(sig)

            for sig in candidates:
                if sig.signal_id in seen:
                    continue
                seen.add(sig.signal_id)
                results.append(simulate(sig, df_15m))

    closed = [r for r in results if r["result"] != "OPEN"]
    wins = [r for r in closed if r["result"] == "WIN"]
    losses = [r for r in closed if r["result"] == "LOSS"]
    be = [r for r in closed if r["result"] == "BREAKEVEN"]
    net_r = sum(r["r"] for r in closed)

    balance = settings.backtest_start_balance
    for r in closed:
        balance *= (1 + settings.risk_per_trade * r["r"])

    breakdown = {}
    for strat in sorted(set(r["strategy"] for r in results)):
        rows = [r for r in closed if r["strategy"] == strat]
        breakdown[strat] = {
            "closed": len(rows),
            "wins": len([x for x in rows if x["result"] == "WIN"]),
            "losses": len([x for x in rows if x["result"] == "LOSS"]),
            "breakeven": len([x for x in rows if x["result"] == "BREAKEVEN"]),
            "net_r": round(sum(x["r"] for x in rows), 2),
        }

    summary = {
        "risk_per_trade": settings.risk_per_trade,
        "rr_target": settings.rr_target,
        "break_even_r": settings.break_even_r,
        "total_trades": len(results),
        "closed": len(closed),
        "wins": len(wins),
        "losses": len(losses),
        "breakeven": len(be),
        "win_rate": round((len(wins) / len(closed) * 100) if closed else 0, 2),
        "net_r": round(net_r, 2),
        "start_balance": settings.backtest_start_balance,
        "end_balance": round(balance, 4),
        "rejected_bias_or_4h": rejected,
        "breakdown": breakdown,
    }

    pd.DataFrame(results).to_csv(settings.backtest_result_file, index=False)
    with open(settings.backtest_summary_file, "w") as f:
        json.dump(summary, f, indent=2)

    log.warning("BACKTEST COMPLETE")
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
