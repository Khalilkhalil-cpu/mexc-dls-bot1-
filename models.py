from dataclasses import dataclass
from typing import Literal, Optional

Side = Literal["buy", "sell"]


@dataclass
class Signal:
    symbol: str
    side: Side
    strategy: str
    timeframe: str
    entry: float
    stop_loss: float
    take_profit: float
    break_even_price: float
    risk_per_unit: float
    signal_time: int
    signal_id: str
    reason: str = ""


@dataclass
class OpenTrade:
    trade_id: str
    symbol: str
    side: Side
    strategy: str
    timeframe: str
    amount: float
    entry: float
    stop_loss: float
    take_profit: float
    break_even_price: float
    risk_per_unit: float
    opened_at: int
    break_even_moved: bool = False
