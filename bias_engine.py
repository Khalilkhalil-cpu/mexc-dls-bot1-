from __future__ import annotations
from typing import Optional, Tuple
import pandas as pd
from spm_engine import latest_spm


def weekly_bias(w1: pd.DataFrame, d1: pd.DataFrame) -> Tuple[Optional[str], str]:
    """Hard weekly rule first. Daily SPM only if weekly is neutral.
    Weekly close above previous weekly high -> buy only, ignore daily.
    Weekly close below previous weekly low -> sell only, ignore daily.
    Otherwise use Daily SPM fallback; if no daily sell SPM, keep buy-only per user's rule.
    """
    if w1 is None or len(w1) < 3:
        return None, "not enough weekly candles"
    last = w1.iloc[-2]
    prev = w1.iloc[-3]
    if float(last.close) > float(prev.high):
        return "buy", "weekly close above previous weekly high -> BUY ONLY"
    if float(last.close) < float(prev.low):
        return "sell", "weekly close below previous weekly low -> SELL ONLY"

    # Weekly did not close above previous high: check Daily SPM.
    daily_last = latest_spm(d1, "1d") if d1 is not None and len(d1) > 10 else None
    if daily_last and daily_last.side == "sell":
        return "sell", "weekly neutral/below prev high and latest Daily SPM is SELL -> SELL ONLY"
    if daily_last and daily_last.side == "buy":
        return "buy", "weekly neutral and latest Daily SPM is BUY -> BUY ONLY"
    return "buy", "weekly neutral and no Daily SELL SPM -> keep BUY ONLY"


def four_h_spm_allows(h4: pd.DataFrame, desired_side: str) -> Tuple[bool, str]:
    spm = latest_spm(h4, "4h") if h4 is not None and len(h4) > 10 else None
    if not spm:
        return False, "no 4H SPM yet"
    if spm.side != desired_side:
        return False, f"latest 4H SPM is {spm.side.upper()}, waiting for {desired_side.upper()} SPM"
    return True, f"latest 4H SPM agrees: {desired_side.upper()}"
