import csv, os, json
from datetime import datetime, timezone
from dataclasses import asdict

os.makedirs("logs", exist_ok=True)
TRADES = "logs/trades.csv"
STATS = "logs/stats.json"

if not os.path.exists(TRADES):
    with open(TRADES, "w", newline="") as f:
        csv.writer(f).writerow(["time_utc","event","strategy","symbol","side","amount","entry","stop","target","exit_price","result","r_multiple","reason","signal_id"])


def _load_stats():
    if os.path.exists(STATS):
        try:
            with open(STATS) as f:
                return json.load(f)
        except Exception:
            pass
    return {"wins":0,"losses":0,"breakeven":0,"total_closed":0,"net_r":0.0}


def _save_stats(s):
    with open(STATS, "w") as f:
        json.dump(s, f, indent=2)


def record_open(signal, amount):
    with open(TRADES, "a", newline="") as f:
        csv.writer(f).writerow([datetime.now(timezone.utc).isoformat(),"OPEN",signal.strategy,signal.symbol,signal.side,amount,signal.entry,signal.stop,signal.target,"","","",signal.reason,signal.signal_id])


def record_close(trade, exit_price: float, reason: str):
    if trade.side == "buy":
        r = (exit_price - trade.entry) / trade.risk_per_unit if trade.risk_per_unit else 0
    else:
        r = (trade.entry - exit_price) / trade.risk_per_unit if trade.risk_per_unit else 0
    result = "win" if r > 0.05 else "loss" if r < -0.05 else "breakeven"
    stats = _load_stats()
    if result == "win": stats["wins"] += 1
    elif result == "loss": stats["losses"] += 1
    else: stats["breakeven"] += 1
    stats["total_closed"] += 1
    stats["net_r"] = float(stats.get("net_r",0)) + float(r)
    _save_stats(stats)
    with open(TRADES, "a", newline="") as f:
        csv.writer(f).writerow([datetime.now(timezone.utc).isoformat(),"CLOSE",trade.strategy,trade.symbol,trade.side,trade.amount,trade.entry,trade.stop,trade.target,exit_price,result,round(r,4),reason,trade.signal_id])
    return stats, r, result


def get_stats():
    return _load_stats()
