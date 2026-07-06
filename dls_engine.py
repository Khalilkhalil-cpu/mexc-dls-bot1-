from __future__ import annotations
from typing import Optional, List, Dict, Any
import pandas as pd
from models import Signal
from config import settings
from spm_engine import latest_spm, has_ec_before_spm


def body_high(row) -> float:
    return max(float(row["open"]), float(row["close"]))


def body_low(row) -> float:
    return min(float(row["open"]), float(row["close"]))


def candle_time(row):
    return row.datetime if "datetime" in row.index else int(row.timestamp)


def _signal(symbol: str, side: str, timeframe: str, entry: float, stop: float, candle_time_value, strategy: str, reason: str) -> Optional[Signal]:
    risk = abs(float(entry) - float(stop))
    if risk <= 0:
        return None
    target = entry + risk * settings.risk_reward if side == "buy" else entry - risk * settings.risk_reward
    be = entry + risk * settings.break_even_r if side == "buy" else entry - risk * settings.break_even_r
    sid = f"{symbol}|{strategy}|{timeframe}|{side}|{pd.Timestamp(candle_time_value).isoformat()}|{round(entry,8)}|{round(stop,8)}"
    return Signal(
        symbol=symbol,
        strategy=strategy,
        side=side,
        timeframe=timeframe,
        entry=float(entry),
        stop=float(stop),
        target=float(target),
        break_even_price=float(be),
        risk_per_unit=float(risk),
        signal_time=candle_time_value,
        signal_id=sid,
        score=100,
        reason=reason,
    )


def detect_dls_type1(symbol: str, df: pd.DataFrame, timeframe: str) -> Optional[Signal]:
    """DLS Type 1: the original immediate-entry model exactly as kept from the old bot."""
    if df is None or len(df) < 3:
        return None
    c1, c2, c3 = df.iloc[-3], df.iloc[-2], df.iloc[-1]
    entry = float(c3.close)
    t = candle_time(c3)

    buy_ok = (
        float(c2.high) > float(c1.high) and
        float(c2.close) < float(c1.high) and
        float(c3.low) < float(c1.low) and
        float(c3.close) > body_high(c2)
    )
    if buy_ok:
        return _signal(symbol, "buy", timeframe, entry, float(c3.low), t, "DLS_TYPE1", "DLS Type 1 buy: immediate entry after C3 close")

    sell_ok = (
        float(c2.low) < float(c1.low) and
        float(c2.close) > float(c1.low) and
        float(c3.high) > float(c1.high) and
        float(c3.close) < body_low(c2)
    )
    if sell_ok:
        return _signal(symbol, "sell", timeframe, entry, float(c3.high), t, "DLS_TYPE1", "DLS Type 1 sell: immediate entry after C3 close")
    return None


def detect_dls_type2_candidate_at(df: pd.DataFrame, timeframe: str, end_idx: int) -> Optional[Dict[str, Any]]:
    """DLS Type 2 candidate ending at end_idx. Needs lower timeframe EC + SPM before entry.

    Type 2 starts like DLS but Candle 3 fails to close beyond Candle 2 open:
    BUY: C3 takes C1 low but does not close above C2 open.
    SELL: C3 takes C1 high but does not close below C2 open.
    """
    if end_idx < 2 or end_idx >= len(df):
        return None
    c1, c2, c3 = df.iloc[end_idx - 2], df.iloc[end_idx - 1], df.iloc[end_idx]
    t = candle_time(c3)

    buy_ok = (
        float(c2.high) > float(c1.high) and
        float(c2.close) < float(c1.high) and
        float(c3.low) < float(c1.low) and
        float(c3.close) <= float(c2.open)
    )
    if buy_ok:
        return {
            "side": "buy", "timeframe": timeframe,
            "c3_low": float(c3.low), "c3_high": float(c3.high),
            "candle3_time": t, "candle3_index": end_idx,
        }

    sell_ok = (
        float(c2.low) < float(c1.low) and
        float(c2.close) > float(c1.low) and
        float(c3.high) > float(c1.high) and
        float(c3.close) >= float(c2.open)
    )
    if sell_ok:
        return {
            "side": "sell", "timeframe": timeframe,
            "c3_low": float(c3.low), "c3_high": float(c3.high),
            "candle3_time": t, "candle3_index": end_idx,
        }
    return None


def recent_dls_type2_candidates(df: pd.DataFrame, timeframe: str) -> List[Dict[str, Any]]:
    if df is None or len(df) < 3:
        return []
    start = max(2, len(df) - int(settings.dls_type2_lookback))
    found: List[Dict[str, Any]] = []
    for idx in range(start, len(df)):
        c = detect_dls_type2_candidate_at(df, timeframe, idx)
        if c:
            found.append(c)
    return found


def detect_dls_type2_confirmed(symbol: str, htf_df: pd.DataFrame, lower_df: pd.DataFrame, timeframe: str, lower_timeframe: str) -> Optional[Signal]:
    candidates = recent_dls_type2_candidates(htf_df, timeframe)
    if not candidates:
        return None

    # Use the latest candidate that gets EC + SPM confirmation.
    for setup in reversed(candidates):
        spm = latest_spm(lower_df, lower_timeframe, side=setup["side"], only_after_time=setup["candle3_time"])
        if not spm:
            continue
        try:
            lower = lower_df.reset_index(drop=True)
            if spm.confirm_index - spm.candle2_index > int(settings.max_type2_confirmation_bars):
                continue
        except Exception:
            pass
        if not has_ec_before_spm(lower_df, spm):
            continue

        entry = float(spm.confirm_close)
        if setup["side"] == "buy":
            # Stop below both original DLS C3 low and lower-TF SPM C2 low.
            stop = min(float(setup["c3_low"]), float(spm.candle2_low))
        else:
            # Stop above both original DLS C3 high and lower-TF SPM C2 high.
            stop = max(float(setup["c3_high"]), float(spm.candle2_high))
        return _signal(
            symbol,
            setup["side"],
            timeframe,
            entry,
            stop,
            spm.confirm_time,
            "DLS_TYPE2",
            f"DLS Type 2 {setup['side']} on {timeframe}; EC + {lower_timeframe} SPM confirmed",
        )
    return None


def detect_dls_signals(symbol: str, dfs: dict) -> List[Signal]:
    signals: List[Signal] = []
    if settings.enable_dls_type1:
        for tf in settings.dls_tf_list:
            if tf in dfs:
                sig = detect_dls_type1(symbol, dfs[tf], tf)
                if sig:
                    signals.append(sig)
    if settings.enable_dls_type2:
        if "1h" in dfs and "15m" in dfs:
            sig = detect_dls_type2_confirmed(symbol, dfs["1h"], dfs["15m"], "1h", "15m")
            if sig:
                signals.append(sig)
        if "2h" in dfs and "30m" in dfs:
            sig = detect_dls_type2_confirmed(symbol, dfs["2h"], dfs["30m"], "2h", "30m")
            if sig:
                signals.append(sig)
    return signals
