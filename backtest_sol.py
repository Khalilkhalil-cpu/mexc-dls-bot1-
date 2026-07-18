import requests
import pandas as pd
import numpy as np
from datetime import datetime, timedelta

# ============================
# CONFIG
# ============================
SYMBOL = "SOL_USDT"
INTERVAL = "3m"
RSI_LEN = 14
LOOKBACK_SWINGS = 50
R_MULTIPLIER = 3.0   # TP = 3R
SL_MULTIPLIER = 1.0  # SL = 1R

# ============================
# FETCH 1 MONTH OF DATA
# ============================
def fetch_1m_klines():
    url = "https://contract.mexc.com/api/v1/contract/kline"
    params = {
        "symbol": SYMBOL,
        "interval": INTERVAL,
        "limit": 2000  # enough for 1 month of 3m candles
    }
    r = requests.get(url, params=params)
    r.raise_for_status()
    data = r.json()["data"]

    df = pd.DataFrame(data)
    df["open"] = df["open"].astype(float)
    df["close"] = df["close"].astype(float)
    df["high"] = df["high"].astype(float)
    df["low"] = df["low"].astype(float)
    df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms")
    return df

# ============================
# INDICATORS
# ============================
def calc_rsi(series, length=14):
    delta = series.diff()
    gain = delta.where(delta > 0, 0).rolling(length).mean()
    loss = -delta.where(delta < 0, 0).rolling(length).mean()
    rs = gain / (loss + 1e-9)
    return 100 - (100 / (1 + rs))

# ============================
# SWING POINTS
# ============================
def find_swings(df):
    swings = []
    for i in range(2, len(df)-2):
        high = df["high"].iloc[i]
        low = df["low"].iloc[i]

        if high > df["high"].iloc[i-2:i].max() and high > df["high"].iloc[i+1:i+3].max():
            swings.append(("high", i))
        if low < df["low"].iloc[i-2:i].min() and low < df["low"].iloc[i+1:i+3].min():
            swings.append(("low", i))
    return swings[-LOOKBACK_SWINGS:]

# ============================
# DIVERGENCE DETECTION
# ============================
def bullish_rsi_div(df):
    swings = [s for s in find_swings(df) if s[0] == "low"]
    if len(swings) < 2:
        return None
    (_, i1), (_, i2) = swings[-2], swings[-1]
    p1, p2 = df["low"].iloc[i1], df["low"].iloc[i2]
    r1, r2 = df["rsi"].iloc[i1], df["rsi"].iloc[i2]
    if p2 < p1 and r2 > r1 + 2:
        return i2
    return None

def bearish_rsi_div(df):
    swings = [s for s in find_swings(df) if s[0] == "high"]
    if len(swings) < 2:
        return None
    (_, i1), (_, i2) = swings[-2], swings[-1]
    p1, p2 = df["high"].iloc[i1], df["high"].iloc[i2]
    r1, r2 = df["rsi"].iloc[i1], df["rsi"].iloc[i2]
    if p2 > p1 and r2 < r1 - 2:
        return i2
    return None

# ============================
# LIQUIDITY SWEEP
# ============================
def bullish_sweep(df, swing_idx):
    swing_low = df["low"].iloc[swing_idx]
    last = df.iloc[-1]
    return last["low"] < swing_low and last["close"] > swing_low

def bearish_sweep(df, swing_idx):
    swing_high = df["high"].iloc[swing_idx]
    last = df.iloc[-1]
    return last["high"] > swing_high and last["close"] < swing_high

# ============================
# FAIR VALUE GAP (FVG)
# ============================
def bullish_fvg(df):
    if len(df) < 3:
        return None
    c0, c2 = df.iloc[-3], df.iloc[-1]
    if c2["low"] > c0["high"]:
        return (c0["high"], c2["low"])
    return None

def bearish_fvg(df):
    if len(df) < 3:
        return None
    c0, c2 = df.iloc[-3], df.iloc[-1]
    if c2["high"] < c0["low"]:
        return (c2["high"], c0["low"])
    return None

def retest_zone(price, zone):
    low, high = zone
    return low <= price <= high

# ============================
# BACKTEST ENGINE
# ============================
def backtest(df):
    trades = []
    position = None

    for i in range(50, len(df)):
        window = df.iloc[:i]
        last = window.iloc[-1]

        # BUY SETUP
        if position is None:
            bull_div = bullish_rsi_div(window)
            if bull_div:
                if bullish_sweep(window, bull_div):
                    fvg = bullish_fvg(window)
                    if fvg and retest_zone(last["close"], fvg):
                        entry = last["close"]
                        sl = window["low"].iloc[bull_div]
                        risk = entry - sl
                        tp = entry + R_MULTIPLIER * risk
                        position = {"side": "long", "entry": entry, "sl": sl, "tp": tp, "start": i}

        # SELL SETUP
        if position is None:
            bear_div = bearish_rsi_div(window)
            if bear_div:
                if bearish_sweep(window, bear_div):
                    fvg = bearish_fvg(window)
                    if fvg and retest_zone(last["close"], fvg):
                        entry = last["close"]
                        sl = window["high"].iloc[bear_div]
                        risk = sl - entry
                        tp = entry - R_MULTIPLIER * risk
                        position = {"side": "short", "entry": entry, "sl": sl, "tp": tp, "start": i}

        # MANAGE POSITION
        if position:
            price = last["close"]
            side = position["side"]

            if side == "long":
                if price <= position["sl"]:
                    trades.append({"side": "long", "result": "SL", "entry": position["entry"], "exit": price})
                    position = None
                elif price >= position["tp"]:
                    trades.append({"side": "long", "result": "TP", "entry": position["entry"], "exit": price})
                    position = None

            if side == "short":
                if price >= position["sl"]:
                    trades.append({"side": "short", "result": "SL", "entry": position["entry"], "exit": price})
                    position = None
                elif price <= position["tp"]:
                    trades.append({"side": "short", "result": "TP", "entry": position["entry"], "exit": price})
                    position = None

    return pd.DataFrame(trades)

# ============================
# RUN BACKTEST
# ============================
df = fetch_1m_klines()
df["rsi"] = calc_rsi(df["close"], RSI_LEN)

results = backtest(df)

print(results)
print("Total trades:", len(results))
print("Win rate:", (results["result"] == "TP").mean())
