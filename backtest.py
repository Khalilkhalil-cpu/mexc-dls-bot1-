import json
import os
import time
from datetime import datetime, timezone, timedelta

import pandas as pd

from bias_engine import final_bias, h4_spm_filter
from config import settings
from dls_engine import detect_dls
from ict_engine import detect_ict_signal
from logger import log
from mexc_client import MexcClient


BACKTEST_VERSION = "one-month-backtest-master-bot-v1"


def simulate_trade(signal, m15: pd.DataFrame):
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
        "stop_loss": signal.stop_loss,
        "take_profit": signal.take_profit,
        "break_even_price": signal.break_even_price,
        "risk_per_unit": signal.risk_per_unit,
        "result": "OPEN",
        "r": 0.0,
        "exit_time": "",
        "exit_price": None,
        "reason": signal.reason,
    }

    for _, bar in future.iterrows():
        high = float(bar["high"])
        low = float(bar["low"])
        dt = str(bar["datetime"])

        if signal.side == "buy":
            if not moved_be and high >= signal.break_even_price:
                moved_be = True

            active_stop = signal.entry if moved_be else signal.stop_loss

            if low <= active_stop:
                result["result"] = "BREAKEVEN" if moved_be else "LOSS"
                result["r"] = 0.0 if moved_be else -1.0
                result["exit_time"] = dt
                result["exit_price"] = active_stop
                return result

            if high >= signal.take_profit:
                result["result"] = "WIN"
                result["r"] = settings.rr_target
                result["exit_time"] = dt
                result["exit_price"] = signal.take_profit
                return result

        else:
            if not moved_be and low <= signal.break_even_price:
                moved_be = True

            active_stop = signal.entry if moved_be else signal.stop_loss

            if high >= active_stop:
                result["result"] = "BREAKEVEN" if moved_be else "LOSS"
                result["r"] = 0.0 if moved_be else -1.0
                result["exit_time"] = dt
                result["exit_price"] = active_stop
                return result

            if low <= signal.take_profit:
                result["result"] = "WIN"
                result["r"] = settings.rr_target
                result["exit_time"] = dt
                result["exit_price"] = signal.take_profit
                return result

    return result


def normalize(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    if "datetime" not in df.columns:
        df["datetime"] = pd.to_datetime(df["timestamp"], unit="ms", utc=True)
    else:
        df["datetime"] = pd.to_datetime(df["datetime"], utc=True)
    return df.sort_values("timestamp").drop_duplicates("timestamp").reset_index(drop=True)


def fetch_all(client: MexcClient, symbol: str):
    log.info("Fetching history for %s", symbol)

    df_1w = normalize(client.fetch_closed_df(symbol, "1w", 80))
    time.sleep(settings.request_delay_seconds)

    df_1d = normalize(client.fetch_closed_df(symbol, "1d", 260))
    time.sleep(settings.request_delay_seconds)

    df_4h = normalize(client.fetch_closed_df(symbol, "4h", 1000))
    time.sleep(settings.request_delay_seconds)

    df_1h = normalize(client.fetch_closed_df(symbol, "1h", 2500))
    time.sleep(settings.request_delay_seconds)

    df_2h = normalize(client.aggregate_2h_from_1h(df_1h, 1000))

    df_15m = normalize(client.fetch_closed_df(symbol, "15m", 8000))
    time.sleep(settings.request_delay_seconds)

    return {
        "1w": df_1w,
        "1d": df_1d,
        "4h": df_4h,
        "1h": df_1h,
        "2h": df_2h,
        "15m": df_15m,
    }


def main():
    os.makedirs("logs", exist_ok=True)

    client = MexcClient()

    symbols = list(settings.backtest_symbol_list)
    days = int(os.getenv("BACKTEST_DAYS", str(settings.backtest_days)))
    start_balance = float(os.getenv("BACKTEST_START_BALANCE", str(settings.backtest_start_balance)))

    now = datetime.now(timezone.utc)
    start = now - timedelta(days=days)

    log.info(
        "Starting 1-month backtest | version=%s | days=%s | risk=%s | RR=%s | BE=%s | symbols=%s",
        BACKTEST_VERSION,
        days,
        settings.risk_per_trade,
        settings.rr_target,
        settings.break_even_r,
        symbols,
    )

    results = []
    rejected = {
        "neutral_bias": 0,
        "h4_spm": 0,
        "side_mismatch": 0,
        "no_signal": 0,
    }

    for symbol in symbols:
        data = fetch_all(client, symbol)

        times = data["15m"][
            (data["15m"]["datetime"] >= pd.Timestamp(start))
            & (data["15m"]["datetime"] <= pd.Timestamp(now))
        ]["datetime"]

        seen = set()

        for idx, t in enumerate(times):
            if idx % 500 == 0:
                log.info("Replay %s progress %s/%s trades=%s", symbol, idx, len(times), len(results))

            if int(t.minute) != 0:
                continue

            hist_1w = data["1w"][data["1w"]["datetime"] <= t]
            hist_1d = data["1d"][data["1d"]["datetime"] <= t]
            hist_4h = data["4h"][data["4h"]["datetime"] <= t]
            hist_1h = data["1h"][data["1h"]["datetime"] <= t]
            hist_2h = data["2h"][data["2h"]["datetime"] <= t]
            hist_15m = data["15m"][data["15m"]["datetime"] <= t]

            if len(hist_1w) < 3 or len(hist_1d) < 30 or len(hist_4h) < 30:
                continue

            bias = final_bias(hist_1w, hist_1d)

            if bias == "neutral":
                rejected["neutral_bias"] += 1
                continue

            ok4h, _ = h4_spm_filter(hist_4h, bias)
            if not ok4h:
                rejected["h4_spm"] += 1
                continue

            signals = []

            if settings.enable_dls:
                sig = detect_dls(symbol, hist_1h, "1h")
                if sig:
                    signals.append(sig)

                if int(t.hour) % 2 == 0:
                    sig = detect_dls(symbol, hist_2h, "2h")
                    if sig:
                        signals.append(sig)

            if settings.enable_ict:
                sig = detect_ict_signal(symbol, bias, hist_4h, hist_1h, hist_15m)
                if sig:
                    signals.append(sig)

            if not signals:
                rejected["no_signal"] += 1
                continue

            for sig in signals:
                if sig.side != bias:
                    rejected["side_mismatch"] += 1
                    continue

                if sig.signal_id in seen:
                    continue

                seen.add(sig.signal_id)
                results.append(simulate_trade(sig, data["15m"]))

    closed = [r for r in results if r["result"] != "OPEN"]
    wins = [r for r in closed if r["result"] == "WIN"]
    losses = [r for r in closed if r["result"] == "LOSS"]
    breakeven = [r for r in closed if r["result"] == "BREAKEVEN"]
    open_at_end = [r for r in results if r["result"] == "OPEN"]

    net_r = sum(r["r"] for r in closed)

    end_balance = start_balance
    for r in closed:
        end_balance *= (1 + settings.risk_per_trade * r["r"])

    breakdown = {}
    for strategy in sorted(set(r["strategy"] for r in results)):
        rows = [r for r in results if r["strategy"] == strategy]
        rows_closed = [r for r in rows if r["result"] != "OPEN"]
        breakdown[strategy] = {
            "total": len(rows),
            "closed": len(rows_closed),
            "wins": len([x for x in rows_closed if x["result"] == "WIN"]),
            "losses": len([x for x in rows_closed if x["result"] == "LOSS"]),
            "breakeven": len([x for x in rows_closed if x["result"] == "BREAKEVEN"]),
            "open": len([x for x in rows if x["result"] == "OPEN"]),
            "net_r": round(sum(x["r"] for x in rows_closed), 2),
        }

    summary = {
        "version": BACKTEST_VERSION,
        "period_days": days,
        "period_start_utc": start.isoformat(),
        "period_end_utc": now.isoformat(),
        "symbols": symbols,
        "risk_per_trade": settings.risk_per_trade,
        "rr_target": settings.rr_target,
        "break_even_r": settings.break_even_r,
        "total_trades": len(results),
        "closed_trades": len(closed),
        "wins": len(wins),
        "losses": len(losses),
        "breakeven": len(breakeven),
        "open_at_end": len(open_at_end),
        "win_rate_closed_percent": round((len(wins) / len(closed) * 100) if closed else 0, 2),
        "net_r_closed": round(net_r, 2),
        "start_balance": start_balance,
        "end_balance_closed_only": round(end_balance, 4),
        "rejected": rejected,
        "strategy_breakdown": breakdown,
        "results_file": settings.backtest_result_file,
        "summary_file": settings.backtest_summary_file,
    }

    pd.DataFrame(results).to_csv(settings.backtest_result_file, index=False)

    with open(settings.backtest_summary_file, "w") as f:
        json.dump(summary, f, indent=2)

    log.warning("BACKTEST COMPLETE")
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
