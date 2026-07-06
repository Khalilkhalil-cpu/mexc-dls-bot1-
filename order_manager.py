from logger import log
from models import Signal, OpenTrade


class OrderManager:
    def __init__(self, client, trades):
        self.client = client
        self.trades = trades

    def open_signal(self, signal: Signal, amount: float):
        side = "buy" if signal.side == "buy" else "sell"
        order = self.client.create_market_order(signal.symbol, side, amount, reduce_only=False)
        trade = self.trades.add_trade(signal, amount)
        log.warning("ORDER OPEN %s order=%s", signal.signal_id, order)
        return trade

    def close_trade(self, trade: OpenTrade, reason: str, price: float):
        side = "sell" if trade.side == "buy" else "buy"
        order = self.client.create_market_order(trade.symbol, side, trade.amount, reduce_only=True)
        self.trades.close_trade(trade, price, reason)
        log.warning("ORDER CLOSE %s reason=%s order=%s", trade.trade_id, reason, order)
