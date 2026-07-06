from __future__ import annotations
import pandas as pd
from typing import Optional, List
from models import SpmPoint
from config import settings


def body_high(c) -> float:
    return max(float(c.open), float(c.close))


def body_low(c) -> float:
    return min(float(c.open), float(c.close))


def is_inside(df: pd.DataFrame, idx: int) -> bool:
    if idx <= 0:
        return False
    c = df.iloc[idx]
    p = df.iloc[idx - 1]
    return float(c.high) <= float(p.high) and float(c.low) >= float(p.low)


def valid_candle1_for_buy(df: pd.DataFrame, c1_idx: int, c2_idx: int) -> bool:
    c1 = df.iloc[c1_idx]
    c2 = df.iloc[c2_idx]
    if is_inside(df, c1_idx):
        return False
    # Candle 2 must not reach Candle 1 low and must not close below Candle 1 body.
    return float(c2.low) > float(c1.low) and float(c2.close) >= body_low(c1)


def valid_candle1_for_sell(df: pd.DataFrame, c1_idx: int, c2_idx: int) -> bool:
    c1 = df.iloc[c1_idx]
    c2 = df.iloc[c2_idx]
    if is_inside(df, c1_idx):
        return False
    # Candle 2 must not reach Candle 1 high and must not close above Candle 1 body.
    return float(c2.high) < float(c1.high) and float(c2.close) <= body_high(c1)


def find_candle1(df: pd.DataFrame, c2_idx: int, side: str) -> Optional[int]:
    start = c2_idx - 1
    end = max(-1, c2_idx - settings.spm_candle1_search_back - 1)
    for i in range(start, end, -1):
        if side == "buy" and valid_candle1_for_buy(df, i, c2_idx):
            return i
        if side == "sell" and valid_candle1_for_sell(df, i, c2_idx):
            return i
    return None


def detect_spms(df: pd.DataFrame, timeframe: str, side: str, only_after_time=None) -> List[SpmPoint]:
    if df is None or len(df) < 10:
        return []
    data = df.reset_index(drop=True).copy()
    start = max(2, len(data) - settings.spm_search_back)
    results: List[SpmPoint] = []

    for c2_idx in range(start, len(data) - 1):
        c1_idx = find_candle1(data, c2_idx, side)
        if c1_idx is None:
            continue
        c1 = data.iloc[c1_idx]
        c2 = data.iloc[c2_idx]
        if only_after_time is not None and getattr(c2, "datetime", None) is not None:
            if c2.datetime < only_after_time:
                continue

        if side == "buy":
            # Confirmation: later candle body close above Candle 1 high.
            level = float(c1.high)
            for k in range(c2_idx + 1, len(data)):
                if float(data.iloc[k].close) > level:
                    results.append(SpmPoint("buy", timeframe, "SPM", c1_idx, c2_idx, k, c1.datetime, c2.datetime, data.iloc[k].datetime, float(c1.high), float(c1.low), float(c2.high), float(c2.low), float(data.iloc[k].close)))
                    break
        else:
            # Confirmation: later candle body close below Candle 1 low.
            level = float(c1.low)
            for k in range(c2_idx + 1, len(data)):
                if float(data.iloc[k].close) < level:
                    results.append(SpmPoint("sell", timeframe, "SPM", c1_idx, c2_idx, k, c1.datetime, c2.datetime, data.iloc[k].datetime, float(c1.high), float(c1.low), float(c2.high), float(c2.low), float(data.iloc[k].close)))
                    break
    return results


def latest_spm(df: pd.DataFrame, timeframe: str, side: Optional[str] = None, only_after_time=None) -> Optional[SpmPoint]:
    sides = [side] if side in ("buy", "sell") else ["buy", "sell"]
    all_spms: List[SpmPoint] = []
    for s in sides:
        all_spms.extend(detect_spms(df, timeframe, s, only_after_time=only_after_time))
    if not all_spms:
        return None
    all_spms.sort(key=lambda x: x.confirm_time)
    return all_spms[-1]


def has_ec_before_spm(df: pd.DataFrame, spm: SpmPoint) -> bool:
    """EC candle must form between Candle 2 and SPM confirmation.
    Buy EC: sweeps previous candle low and closes bullish above previous open.
    Sell EC: sweeps previous candle high and closes bearish below previous open.
    """
    data = df.reset_index(drop=True)
    for i in range(spm.candle2_index + 1, spm.confirm_index + 1):
        if i <= 0:
            continue
        c = data.iloc[i]
        p = data.iloc[i - 1]
        if spm.side == "buy":
            if float(c.low) < float(p.low) and float(c.close) > float(c.open) and float(c.close) > float(p.open):
                return True
        else:
            if float(c.high) > float(p.high) and float(c.close) < float(c.open) and float(c.close) < float(p.open):
                return True
    return False
