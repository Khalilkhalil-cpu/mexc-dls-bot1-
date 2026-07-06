from typing import Optional

import pandas as pd

from config import settings
from models import Signal


def body_high(row) -> float:
    return max(float(row.open), float(row.close))


def body_low(row) -> float:
    return min(float(row.open), float(row.close))


def make_signal(symbol, side, strategy, timeframe, entry, stop, signal_time, reason) -> Optional[Signal]:
    if side == "buy":
        risk = entry - stop
        if risk <= 0:
            return None
        target = entry + risk * settings.rr_target
        be = entry + risk * settings.break_even_r
    else:
        risk = stop - entry
        if risk <= 0:
            return None
        target = entry - risk * settings.rr_target
        be = entry - risk * settings.break_even_r

    return Signal(
        symbol=symbol,
        side=side,
        strategy=strategy,
        timeframe=timeframe,
        entry=float(entry),
        stop_loss=float(stop),
        take_profit=float(target),
        break_even_price=float(be),
        risk_per_unit=float(risk),
        signal_time=int(signal_time),
        signal_id=f"{symbol}|{strategy}|{timeframe}|{side}|{int(signal_time)}|{round(entry, 8)}|{round(stop, 8)}",
        reason=reason,
    )


def detect_dls(symbol: str, df: pd.DataFrame, timeframe: str) -> Optional[Signal]:
    """
    Extended DLS Type 1 and Type 2.

    BUY:
      C2 sweeps C1 high and closes below C1 high.
      Optional extra candles after C2 must stay inside C1 high/low.
      Final candle takes C1 low.
      Type 1: final candle closes above C2 body top => immediate entry.
      Type 2: final candle does not close above C2 open => delayed model, but this live version allows it as its own signal.

    SELL is opposite.

    Stop:
      BUY stop below final/Candle3 low.
      SELL stop above final/Candle3 high.
    """
    d = df.copy().sort_values("timestamp").reset_index(drop=True)
    if len(d) < 3:
        return None

    last_i = len(d) - 1
    final_c = d.iloc[last_i]
    first_c1 = max(0, last_i - settings.dls_max_extra_candles - 2)

    for c1_i in range(first_c1, last_i - 1):
        c1 = d.iloc[c1_i]
        c1_high, c1_low = float(c1.high), float(c1.low)

        for c2_i in range(c1_i + 1, last_i):
            c2 = d.iloc[c2_i]
            middle = d.iloc[c2_i + 1:last_i]

            if not middle.empty:
                if bool(((middle.high >= c1_high) | (middle.low <= c1_low)).any()):
                    continue

            c2_high, c2_low = float(c2.high), float(c2.low)
            c2_close, c2_open = float(c2.close), float(c2.open)
            c2_body_top = body_high(c2)
            c2_body_bottom = body_low(c2)

            f_high, f_low, f_close = float(final_c.high), float(final_c.low), float(final_c.close)
            signal_time = int(final_c.timestamp)

            buy_structure = c2_high > c1_high and c2_close < c1_high and f_low < c1_low
            if buy_structure:
                if settings.enable_dls_type1 and f_close > c2_body_top:
                    return make_signal(symbol, "buy", "DLS_TYPE1", timeframe, f_close, f_low, signal_time, "extended DLS Type1 buy")
                if settings.enable_dls_type2 and f_close <= c2_open:
                    return make_signal(symbol, "buy", "DLS_TYPE2", timeframe, f_close, f_low, signal_time, "extended DLS Type2 buy")

            sell_structure = c2_low < c1_low and c2_close > c1_low and f_high > c1_high
            if sell_structure:
                if settings.enable_dls_type1 and f_close < c2_body_bottom:
                    return make_signal(symbol, "sell", "DLS_TYPE1", timeframe, f_close, f_high, signal_time, "extended DLS Type1 sell")
                if settings.enable_dls_type2 and f_close >= c2_open:
                    return make_signal(symbol, "sell", "DLS_TYPE2", timeframe, f_close, f_high, signal_time, "extended DLS Type2 sell")

    return None
