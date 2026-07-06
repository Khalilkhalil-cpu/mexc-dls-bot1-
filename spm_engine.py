from dataclasses import dataclass
from typing import Literal, Optional

import pandas as pd

Side = Literal["buy", "sell"]


@dataclass
class SPM:
    side: Side
    confirmed_time: int
    c1_time: int
    c2_time: int
    c1_level: float
    c2_extreme: float


def body_high(row) -> float:
    return max(float(row["open"]), float(row["close"]))


def body_low(row) -> float:
    return min(float(row["open"]), float(row["close"]))


def is_inside_candle(df: pd.DataFrame, i: int) -> bool:
    if i <= 0:
        return False
    c = df.iloc[i]
    p = df.iloc[i - 1]
    return float(c.high) <= float(p.high) and float(c.low) >= float(p.low)


def find_valid_bull_c1(df: pd.DataFrame, c2_i: int, max_back: int = 50) -> Optional[int]:
    c2 = df.iloc[c2_i]
    start = max(1, c2_i - max_back)
    for i in range(c2_i - 1, start - 1, -1):
        c1 = df.iloc[i]
        if is_inside_candle(df, i):
            continue
        if float(c2.low) <= float(c1.low):
            continue
        if float(c2.close) < body_low(c1):
            continue
        return i
    return None


def find_valid_bear_c1(df: pd.DataFrame, c2_i: int, max_back: int = 50) -> Optional[int]:
    c2 = df.iloc[c2_i]
    start = max(1, c2_i - max_back)
    for i in range(c2_i - 1, start - 1, -1):
        c1 = df.iloc[i]
        if is_inside_candle(df, i):
            continue
        if float(c2.high) >= float(c1.high):
            continue
        if float(c2.close) > body_high(c1):
            continue
        return i
    return None


def detect_last_spm(df: pd.DataFrame, search_back: int = 160, candle1_back: int = 50) -> Optional[SPM]:
    data = df.copy().sort_values("timestamp").reset_index(drop=True)
    if len(data) < 30:
        return None

    start = max(5, len(data) - search_back)
    spms: list[SPM] = []

    for c2_i in range(start, len(data) - 1):
        left = max(0, c2_i - 20)
        right = min(len(data), c2_i + 21)
        window = data.iloc[left:right]

        if float(data.iloc[c2_i].low) == float(window.low.min()):
            c1_i = find_valid_bull_c1(data, c2_i, candle1_back)
            if c1_i is not None:
                c1 = data.iloc[c1_i]
                level = float(c1.high)
                after = data.iloc[c2_i + 1:]
                confirms = after[after.close > level]
                if not confirms.empty:
                    conf = confirms.iloc[0]
                    spms.append(SPM(
                        side="buy",
                        confirmed_time=int(conf.timestamp),
                        c1_time=int(c1.timestamp),
                        c2_time=int(data.iloc[c2_i].timestamp),
                        c1_level=level,
                        c2_extreme=float(data.iloc[c2_i].low),
                    ))

        if float(data.iloc[c2_i].high) == float(window.high.max()):
            c1_i = find_valid_bear_c1(data, c2_i, candle1_back)
            if c1_i is not None:
                c1 = data.iloc[c1_i]
                level = float(c1.low)
                after = data.iloc[c2_i + 1:]
                confirms = after[after.close < level]
                if not confirms.empty:
                    conf = confirms.iloc[0]
                    spms.append(SPM(
                        side="sell",
                        confirmed_time=int(conf.timestamp),
                        c1_time=int(c1.timestamp),
                        c2_time=int(data.iloc[c2_i].timestamp),
                        c1_level=level,
                        c2_extreme=float(data.iloc[c2_i].high),
                    ))

    if not spms:
        return None
    spms.sort(key=lambda x: x.confirmed_time)
    return spms[-1]
