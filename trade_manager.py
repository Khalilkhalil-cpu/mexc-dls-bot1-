from dataclasses import dataclass
from typing import List
from models import Signal
from journal import record_open, record_close
from logger import log

@dataclass
class OpenTrade:
    symbol: str
    strategy: str
    side: str
    amount: float
    entry: float
    stop: float
    target: float
    break_even_price: float
    risk_per_unit: float
    signal_id: str
    break_even_moved: bool = False

class TradeManager:
    def __init__(self):
        self.open_trades: List[OpenTrade] = []

    def add(self, signal: Signal, amount: float):
        risk = signal.risk_per_unit or abs(signal.entry - signal.stop)
        be = signal.break_even_price or (signal.entry + risk * 0.82 if signal.side == "buy" else signal.entry - risk * 0.82)
        t = OpenTrade(signal.symbol, signal.strategy, signal.side, amount, signal.entry, signal.stop, signal.target, be, risk, signal.signal_id)
        self.open_trades.append(t)
        record_open(signal, amount)
        return t

    def has_signal(self, signal_id: str) -> bool:
        return any(t.signal_id == signal_id for t in self.open_trades)

    def manage(self, client):
        for t in list(self.open_trades):
            try:
                price = client.market_price(t.symbol)
                if not t.break_even_moved:
                    if (t.side == "buy" and price >= t.break_even_price) or (t.side == "sell" and price <= t.break_even_price):
                        t.stop = t.entry
                        t.break_even_moved = True
                        log.info("Moved SL to BE: %s", t.signal_id)
                reason = None
                exit_price = None
                if t.side == "buy":
                    if price <= t.stop:
                        reason, exit_price = "STOP_LOSS", t.stop
                    elif price >= t.target:
                        reason, exit_price = "TAKE_PROFIT", t.target
                else:
                    if price >= t.stop:
                        reason, exit_price = "STOP_LOSS", t.stop
                    elif price <= t.target:
                        reason, exit_price = "TAKE_PROFIT", t.target
                if reason:
                    close_side = "sell" if t.side == "buy" else "buy"
                    client.create_market_order(t.symbol, close_side, t.amount, reduce_only=True)
                    stats, r, result = record_close(t, exit_price, reason)
                    log.warning("Trade closed %s %s R=%.2f | wins=%s losses=%s", t.symbol, result, r, stats["wins"], stats["losses"])
                    self.open_trades.remove(t)
            except Exception as e:
                log.exception("Error managing trade %s: %s", t.signal_id, e)
