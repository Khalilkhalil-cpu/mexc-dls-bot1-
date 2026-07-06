from typing import Optional
import pandas as pd

from config import settings
from models import Signal


def make_signal(symbol, side, entry, stop, signal_time, reason) -> Optional[Signal]:
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
        strategy="ICT",
        timeframe="15m",
        entry=float(entry),
        stop_loss=float(stop),
        take_profit=float(target),
        break_even_price=float(be),
        risk_per_unit=float(risk),
        signal_time=int(signal_time),
        signal_id=f"{symbol}|ICT|{side}|{int(signal_time)}|{round(entry, 8)}|{round(stop, 8)}",
        reason=reason,
    )


def detect_fvg(df: pd.DataFrame, side: str, lookback: int = 80):
    if len(df) < 5:
        return None
    start = max(2, len(df) - lookback)
    zones = []
    for i in range(start, len(df) - 1):
        c0, c2 = df.iloc[i - 2], df.iloc[i]
        if side == "buy" and float(c2.low) > float(c0.high):
            zones.append((i, float(c0.high), float(c2.low)))
        if side == "sell" and float(c2.high) < float(c0.low):
            zones.append((i, float(c2.high), float(c0.low)))
    return zones[-1] if zones else None


def zone_touched(df: pd.DataFrame, zone):
    if not zone:
        return False
    i, low, high = zone
    recent = df.iloc[i + 1:]
    if recent.empty:
        return False
    return bool(((recent.low <= high) & (recent.high >= low)).any())


def stop_hunt(df: pd.DataFrame, side: str, lookback: int = 30):
    if len(df) < lookback:
        return None
    recent = df.iloc[-lookback:]
    for idx in range(max(3, len(recent) - 5), len(recent)):
        cur = recent.iloc[idx]
        prev = recent.iloc[max(0, idx - 10):idx]
        if prev.empty:
            continue
        if side == "buy" and float(cur.low) < float(prev.low.min()) and float(cur.close) > float(cur.low):
            return cur
        if side == "sell" and float(cur.high) > float(prev.high.max()) and float(cur.close) < float(cur.high):
            return cur
    return None


def cisd(df: pd.DataFrame, side: str, lookback: int = 20):
    recent = df.iloc[-lookback:].copy()
    if len(recent) < 5:
        return None
    for i in range(3, len(recent)):
        c = recent.iloc[i]
        prev = recent.iloc[:i]
        if side == "buy":
            bears = prev[prev.close < prev.open]
            if bears.empty:
                continue
            key = bears.iloc[-1]
            if float(c.close) > float(key.open):
                return c, float(recent.low.min())
        else:
            bulls = prev[prev.close > prev.open]
            if bulls.empty:
                continue
            key = bulls.iloc[-1]
            if float(c.close) < float(key.open):
                return c, float(recent.high.max())
    return None


def detect_ict_signal(symbol: str, side: str, h4: pd.DataFrame, h1: pd.DataFrame, m15: pd.DataFrame) -> Optional[Signal]:
    fvg = detect_fvg(h4, side)
    if not fvg or not zone_touched(h4, fvg):
        return None

    sh = stop_hunt(h1, side)
    if sh is None:
        return None

    ci = cisd(m15, side)
    if ci is None:
        return None

    c, stop = ci
    entry = float(c.close)
    return make_signal(symbol, side, entry, stop, int(c.timestamp), "ICT: 4H FVG + 1H stop hunt + 15M CISD")
