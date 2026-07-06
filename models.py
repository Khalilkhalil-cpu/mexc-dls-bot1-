from dataclasses import dataclass
from typing import Literal, Optional

Side = Literal["buy", "sell"]

@dataclass
class Signal:
    symbol: str
    side: Side
    entry: float
    stop: float
    target: float
    signal_time: object
    signal_id: str
    strategy: str = "ICT"
    timeframe: str = ""
    break_even_price: float = 0.0
    risk_per_unit: float = 0.0
    score: int = 100
    reason: str = ""

@dataclass
class SpmPoint:
    side: Side
    timeframe: str
    model: str
    candle1_index: int
    candle2_index: int
    confirm_index: int
    candle1_time: object
    candle2_time: object
    confirm_time: object
    candle1_high: float
    candle1_low: float
    candle2_high: float
    candle2_low: float
    confirm_close: float
