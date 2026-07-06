from typing import Literal

import pandas as pd

from config import settings
from spm_engine import detect_last_spm

Bias = Literal["buy", "sell", "neutral"]


def weekly_bias(df_1w: pd.DataFrame) -> Bias:
    if len(df_1w) < 3:
        return "neutral"
    last_week = df_1w.iloc[-2] if len(df_1w) > 2 else df_1w.iloc[-1]
    prev_week = df_1w.iloc[-3] if len(df_1w) > 2 else df_1w.iloc[-2]

    if float(last_week.close) > float(prev_week.high):
        return "buy"
    if float(last_week.close) < float(prev_week.low):
        return "sell"
    return "neutral"


def daily_spm_fallback(df_1d: pd.DataFrame, weekly: Bias) -> Bias:
    # Only used when weekly is neutral.
    if weekly != "neutral":
        return weekly
    spm = detect_last_spm(df_1d, settings.spm_search_back, settings.spm_candle1_search_back)
    return spm.side if spm else "neutral"


def final_bias(df_1w: pd.DataFrame, df_1d: pd.DataFrame) -> Bias:
    wb = weekly_bias(df_1w)
    if wb != "neutral":
        return wb
    return daily_spm_fallback(df_1d, wb)


def h4_spm_filter(df_4h: pd.DataFrame, desired_side: str) -> tuple[bool, str]:
    spm = detect_last_spm(df_4h, settings.spm_search_back, settings.spm_candle1_search_back)
    if not spm:
        return False, "no 4H SPM"
    if spm.side != desired_side:
        return False, f"4H SPM mismatch: last={spm.side}, signal={desired_side}"
    return True, f"4H SPM matched {spm.side}"
