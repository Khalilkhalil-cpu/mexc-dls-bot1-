import os
import time
import requests
import pandas as pd

# ================== CONFIG ==================
SYMBOL = os.getenv("SYMBOL", "SOL_USDT")   # MEXC futures symbol
INTERVAL = os.getenv("INTERVAL", "1m")
EMA_LEN = int(os.getenv("EMA_LEN", "200"))
CHECK_INTERVAL = int(os.getenv("CHECK_INTERVAL", "5"))  # seconds
R_MULTIPLIER = float(os.getenv("R_MULTIPLIER", "2.0"))
POSITION_SIZE = float(os.getenv("POSITION_SIZE", "1"))  # contracts, adjust for your size

MEXC_API_KEY = os.getenv("MEXC_API_KEY", "")
MEXC_API_SECRET = os.getenv("MEXC_API_SECRET", "")

# ================== DATA FETCH ==================
def fetch_klines(limit=EMA_LEN + 10):
    url = "https://contract.mexc.com/api/v1/contract/kline"
    params = {
        "symbol": SYMBOL,
        "interval": INTERVAL,
        "limit": limit
    }
    r = requests.get(url, params=params, timeout=10)
    r.raise_for_status()
    data = r.json()["data"]

    df = pd.DataFrame(data)
    df["open"] = df["open"].astype(float)
    df["close"] = df["close"].astype(float)
    df["high"] = df["high"].astype(float)
    df["low"] = df["low"].astype(float)
    return df

def add_ema200(df):
    df["ema200"] = df["close"].ewm(span=EMA_LEN, adjust=False).mean()
    return df

# ================== PATTERN LOGIC ==================
def touches_ema(row):
    return row["low"] <= row["ema200"] <= row["high"]

def rejection_short(row):
    body = abs(row["close"] - row["open"])
    upper_wick = row["high"] - max(row["close"], row["open"])
    return (
        row["close"] < row["ema200"] and
        touches_ema(row) and
        upper_wick > body * 1.5 and
        row["close"] < row["open"]
    )

def rejection_long(row):
    body = abs(row["close"] - row["open"])
    lower_wick = min(row["close"], row["open"]) - row["low"]
    return (
        row["close"] > row["ema200"] and
        touches_ema(row) and
        lower_wick > body * 1.5 and
        row["close"] > row["open"]
    )

def build_short_trade(candle):
    entry = candle["close"]
    sl = candle["high"]
    risk = sl - entry
    tp = entry - R_MULTIPLIER * risk
    return {"side": "short", "entry": entry, "sl": sl, "tp": tp}

def build_long_trade(candle):
    entry = candle["close"]
    sl = candle["low"]
    risk = entry - sl
    tp = entry + R_MULTIPLIER * risk
    return {"side": "long", "entry": entry, "sl": sl, "tp": tp}

# ================== ORDER EXECUTION (PLACEHOLDER) ==================
def place_futures_order(side, size):
    # side: "buy" or "sell"
    # TODO: replace with real MEXC futures REST call using MEXC_API_KEY / MEXC_API_SECRET
    print(f"[ORDER] {side.upper()} {size} {SYMBOL}")

def close_futures_position(side, size):
    # close opposite side
    close_side = "sell" if side == "long" else "buy"
    print(f"[CLOSE] {close_side.upper()} {size} {SYMBOL}")
    # TODO: send close order to MEXC

# ================== BOT LOOP ==================
def run_bot():
    position = None  # {"side": "long"/"short", "entry": float, "sl": float, "tp": float}

    while True:
        try:
            df = fetch_klines()
            df = add_ema200(df)
            last = df.iloc[-1]

            if position is None:
                # Look for new setup
                if rejection_short(last):
                    trade = build_short_trade(last)
                    place_futures_order("sell", POSITION_SIZE)
                    position = trade
                    print("[ENTER SHORT]", trade)

                elif rejection_long(last):
                    trade = build_long_trade(last)
                    place_futures_order("buy", POSITION_SIZE)
                    position = trade
                    print("[ENTER LONG]", trade)

            else:
                # Manage open trade
                price = last["close"]
                side = position["side"]
                sl = position["sl"]
                tp = position["tp"]

                if side == "long":
                    if price <= sl:
                        print("[LONG STOP HIT]", price)
                        close_futures_position(side, POSITION_SIZE)
                        position = None
                    elif price >= tp:
                        print("[LONG TP HIT 2R]", price)
                        close_futures_position(side, POSITION_SIZE)
                        position = None
                else:  # short
                    if price >= sl:
                        print("[SHORT STOP HIT]", price)
                        close_futures_position(side, POSITION_SIZE)
                        position = None
                    elif price <= tp:
                        print("[SHORT TP HIT 2R]", price)
                        close_futures_position(side, POSITION_SIZE)
                        position = None

            time.sleep(CHECK_INTERVAL)

        except Exception as e:
            print("[ERROR]", e)
            time.sleep(10)

if __name__ == "__main__":
    print("Starting SOLUSDT EMA200 futures bot...")
    run_bot()
