from datetime import datetime, timezone
from typing import Iterable

from config import settings
from models import OpenTrade, Signal


def position_size(client, signal: Signal) -> float:
    balance = client.balance_usdt()
    risk_amount = balance * settings.risk_per_trade
    return client.contracts_from_risk(signal.symbol, signal.entry, signal.stop_loss, risk_amount)


def should_move_to_break_even(trade: OpenTrade, price: float) -> bool:
    if trade.break_even_moved:
        return False
    if trade.side == "buy":
        return price >= trade.break_even_price
    return price <= trade.break_even_price


def exit_reason(trade: OpenTrade, price: float):
    if trade.side == "buy":
        if price <= trade.stop_loss:
            return "STOP_LOSS"
        if price >= trade.take_profit:
            return "TAKE_PROFIT"
    else:
        if price >= trade.stop_loss:
            return "STOP_LOSS"
        if price <= trade.take_profit:
            return "TAKE_PROFIT"
    return None
