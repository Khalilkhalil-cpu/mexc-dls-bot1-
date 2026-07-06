import csv
import json
import os
from dataclasses import asdict
from datetime import datetime, timezone
from typing import List

from logger import log
from models import OpenTrade, Signal


class TradeManager:
    def __init__(self):
        self.open_trades: List[OpenTrade] = []
        os.makedirs("logs", exist_ok=True)
        self.trades_path = "logs/trades.csv"
        self.stats_path = "logs/stats.json"
        if not os.path.exists(self.trades_path):
            with open(self.trades_path, "w", newline="") as f:
                writer = csv.writer(f)
                writer.writerow(["closed_at", "trade_id", "symbol", "strategy", "side", "entry", "exit", "result", "pnl_estimate"])

        if not os.path.exists(self.stats_path):
            self.save_stats({"wins": 0, "losses": 0, "breakeven": 0, "closed": 0})

    def save_stats(self, stats):
        with open(self.stats_path, "w") as f:
            json.dump(stats, f, indent=2)

    def load_stats(self):
        with open(self.stats_path, "r") as f:
            return json.load(f)

    def has_open_symbol(self, symbol: str) -> bool:
        return any(t.symbol == symbol for t in self.open_trades)

    def add_trade(self, signal: Signal, amount: float) -> OpenTrade:
        trade = OpenTrade(
            trade_id=signal.signal_id,
            symbol=signal.symbol,
            side=signal.side,
            strategy=signal.strategy,
            timeframe=signal.timeframe,
            amount=amount,
            entry=signal.entry,
            stop_loss=signal.stop_loss,
            take_profit=signal.take_profit,
            break_even_price=signal.break_even_price,
            risk_per_unit=signal.risk_per_unit,
            opened_at=signal.signal_time,
        )
        self.open_trades.append(trade)
        log.warning("TRADE OPENED %s", asdict(trade))
        return trade

    def close_trade(self, trade: OpenTrade, exit_price: float, result: str):
        if trade.side == "buy":
            pnl = (exit_price - trade.entry) * trade.amount
        else:
            pnl = (trade.entry - exit_price) * trade.amount

        with open(self.trades_path, "a", newline="") as f:
            writer = csv.writer(f)
            writer.writerow([datetime.now(timezone.utc).isoformat(), trade.trade_id, trade.symbol, trade.strategy, trade.side, trade.entry, exit_price, result, pnl])

        stats = self.load_stats()
        stats["closed"] = stats.get("closed", 0) + 1
        if result == "TAKE_PROFIT":
            stats["wins"] = stats.get("wins", 0) + 1
        elif result == "STOP_LOSS":
            if trade.break_even_moved:
                stats["breakeven"] = stats.get("breakeven", 0) + 1
            else:
                stats["losses"] = stats.get("losses", 0) + 1
        self.save_stats(stats)

        log.warning("TRADE CLOSED %s result=%s exit=%s pnl=%s stats=%s", trade.trade_id, result, exit_price, pnl, stats)
        self.open_trades = [t for t in self.open_trades if t.trade_id != trade.trade_id]
