import pandas as pd
from models import Signal
from config import settings
from zoneinfo import ZoneInfo

# Session defaults: London local 02:00-05:00, New York local 02:00-11:30.
# Railway optional variables via config/settings can override later if added.
LONDON_TZ = ZoneInfo("Europe/London")
NY_TZ = ZoneInfo("America/New_York")



def body_bull(c): return c.close > c.open
def body_bear(c): return c.close < c.open

def _to_zone(ts, tz):
    t = pd.Timestamp(ts)
    if t.tzinfo is None:
        t = t.tz_localize("UTC")
    return t.tz_convert(tz)

def _minutes(t):
    return t.hour * 60 + t.minute

def _enabled_sessions():
    raw = getattr(settings, "trading_sessions", "LONDON,NEWYORK")
    return {x.strip().upper() for x in str(raw).split(",") if x.strip()}

def in_london_window(ts) -> bool:
    # London local time: 02:00 to 05:00
    london = _to_zone(ts, LONDON_TZ)
    m = _minutes(london)
    return (2 * 60) <= m <= (5 * 60)

def in_newyork_window(ts) -> bool:
    # New York local time: 02:00 to 11:30
    ny = _to_zone(ts, NY_TZ)
    m = _minutes(ny)
    return (2 * 60) <= m <= (11 * 60 + 30)

def active_session(ts):
    enabled = _enabled_sessions()
    active = []
    if "LONDON" in enabled and in_london_window(ts):
        active.append("LONDON")
    if ("NEWYORK" in enabled or "NEW_YORK" in enabled or "NY" in enabled) and in_newyork_window(ts):
        active.append("NEWYORK")
    return "+".join(active) if active else "NONE"

def in_trading_session(ts) -> bool:
    return active_session(ts) != "NONE"

def daily_bias(d1: pd.DataFrame):
    if len(d1) < 5:
        return None, 0, "not enough daily candles"
    prev = d1.iloc[-2]
    before = d1.iloc[-3]
    score = 0
    side = None
    reason = []
    if prev.close > before.high:
        side = "buy"; score += 35; reason.append("daily body closed above previous high")
    elif prev.close < before.low:
        side = "sell"; score += 35; reason.append("daily body closed below previous low")
    else:
        return None, 0, "no daily candle bias"
    # candle close strength
    if side == "buy" and prev.close > (prev.open + (prev.high - prev.low) * 0.6):
        score += 10; reason.append("strong bullish daily close")
    if side == "sell" and prev.close < (prev.open - (prev.open - prev.low) * 0.6):
        score += 10; reason.append("strong bearish daily close")
    return side, score, "; ".join(reason)

def find_latest_fvg(df: pd.DataFrame, side: str, lookback: int = 80):
    # bullish FVG: candle i low > candle i-2 high, zone [i-2 high, i low]
    # bearish FVG: candle i high < candle i-2 low, zone [i high, i-2 low]
    start = max(2, len(df) - lookback)
    zones = []
    for i in range(start, len(df)-1):
        c0, c2 = df.iloc[i-2], df.iloc[i]
        if side == "buy" and c2.low > c0.high:
            zones.append((i, float(c0.high), float(c2.low)))
        if side == "sell" and c2.high < c0.low:
            zones.append((i, float(c2.high), float(c0.low)))
    return zones[-1] if zones else None

def price_touched_zone(df: pd.DataFrame, zone, after_index: int):
    if not zone: return False
    _, low, high = zone
    recent = df.iloc[after_index+1:]
    if recent.empty: return False
    return bool(((recent.low <= high) & (recent.high >= low)).any())

def stop_hunt(df: pd.DataFrame, side: str, lookback: int = 30):
    if len(df) < lookback + 3: return None
    recent = df.iloc[-lookback:]
    # simple swing sweep/reject in last 5 candles
    for idx in range(len(recent)-5, len(recent)):
        if idx < 3: continue
        cur = recent.iloc[idx]
        prev_lows = recent.iloc[max(0, idx-10):idx].low
        prev_highs = recent.iloc[max(0, idx-10):idx].high
        if side == "buy" and cur.low < prev_lows.min() and cur.close > cur.low:
            return float(cur.low), cur.datetime
        if side == "sell" and cur.high > prev_highs.max() and cur.close < cur.high:
            return float(cur.high), cur.datetime
    return None

def cisd(df: pd.DataFrame, side: str, lookback: int = 20):
    recent = df.iloc[-lookback:].copy()
    if len(recent) < 5: return None
    for i in range(3, len(recent)):
        c = recent.iloc[i]
        prev = recent.iloc[:i]
        if side == "buy":
            bears = prev[prev.close < prev.open]
            if bears.empty: continue
            key = bears.iloc[-1]
            if c.close > key.open:
                return {"index": df.index[-lookback+i], "entry": float(c.close), "stop": float(recent.low.min()), "time": c.datetime}
        else:
            bulls = prev[prev.close > prev.open]
            if bulls.empty: continue
            key = bulls.iloc[-1]
            if c.close < key.open:
                return {"index": df.index[-lookback+i], "entry": float(c.close), "stop": float(recent.high.max()), "time": c.datetime}
    return None

def propulsion_block(df: pd.DataFrame, side: str, cisd_info):
    if not cisd_info: return None
    tail = df.loc[cisd_info["index"]:].copy()
    if len(tail) < 4: return None
    low = float(tail.low.min())
    high = float(tail.high.max())
    if high <= low: return None
    if side == "buy":
        fib62 = high - (high - low) * 0.62
        fib79 = high - (high - low) * 0.79
        last = df.iloc[-1]
        if last.close < fib79:
            return None
        if last.low <= fib62 and last.close > last.open:
            return float(last.close), low, last.datetime
    else:
        fib62 = low + (high - low) * 0.62
        fib79 = low + (high - low) * 0.79
        last = df.iloc[-1]
        if last.close > fib79:
            return None
        if last.high >= fib62 and last.close < last.open:
            return float(last.close), high, last.datetime
    return None

def detect_signal(symbol: str, d1, h4, h1, m15):
    side, score, reason = daily_bias(d1)
    if not side: return None, reason
    last_time = m15.iloc[-1].datetime
    session = active_session(last_time)
    if session == "NONE":
        enabled = ",".join(sorted(_enabled_sessions()))
        london_time = _to_zone(last_time, LONDON_TZ).strftime("%H:%M")
        ny_time = _to_zone(last_time, NY_TZ).strftime("%H:%M")
        return None, f"outside London/NewYork session | enabled={enabled} | London={london_time} NY={ny_time} | {reason}"
    fvg = find_latest_fvg(h4, side)
    if not fvg:
        return None, f"no 4H FVG | {reason}"
    if not price_touched_zone(h4, fvg, fvg[0]):
        return None, f"4H FVG not touched | {reason}"
    score += 20
    sh = stop_hunt(h1, side)
    if not sh:
        return None, f"no 1H stop hunt | {reason}"
    score += 15
    ci = cisd(m15, side)
    if not ci:
        return None, f"no 15M CISD | {reason}"
    score += 15
    entry, stop, signal_time = ci["entry"], ci["stop"], ci.get("time")
    if settings.entry_mode == "pb":
        pb = propulsion_block(m15, side, ci)
        if not pb:
            return None, f"no 15M propulsion block | {reason}"
        entry, stop, signal_time = pb
        score += 15
    if side == "buy":
        if stop >= entry: return None, "bad buy stop"
        target = entry + (entry - stop) * settings.rr_target
    else:
        if stop <= entry: return None, "bad sell stop"
        target = entry - (stop - entry) * settings.rr_target
    if score < settings.min_score:
        return None, f"score too low {score} | {reason}"
    
    signal_id = f"{symbol}|{side}|{pd.Timestamp(signal_time).isoformat()}|{round(entry, 8)}|{round(stop, 8)}"
    return Signal(
        symbol=symbol,
        side=side,
        score=score,
        entry=entry,
        stop=stop,
        target=target,
        reason=reason + f"; active session={session}; 4H FVG + 1H stop hunt + 15M CISD/PB",
        signal_time=signal_time,
        signal_id=signal_id,
    ), "signal"
