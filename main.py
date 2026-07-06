import time
from datetime import datetime, timezone, timedelta
from zoneinfo import ZoneInfo

from config import settings
from logger import log
from mexc_client import MexcClient
from risk import position_size
from trade_manager import TradeManager
from bias_engine import weekly_bias, four_h_spm_allows
from dls_engine import detect_dls_signals
from ict_engine import detect_signal as detect_ict_signal
from journal import get_stats

VERSION = "master-ict-dls-bot-v1"
SEEN_SIGNALS = set()
LAST_TRADE = {}
LAST_NY_SESSION_DATE = None


def in_ny_session():
    now = datetime.now(ZoneInfo("America/New_York"))
    start = now.replace(hour=settings.ny_start_hour, minute=0, second=0, microsecond=0)
    end = now.replace(hour=settings.ny_end_hour, minute=settings.ny_end_minute, second=0, microsecond=0)
    return start <= now <= end


def reset_seen_at_ny_open():
    global LAST_NY_SESSION_DATE
    now = datetime.now(ZoneInfo("America/New_York"))
    if now.hour >= settings.ny_start_hour:
        if LAST_NY_SESSION_DATE != now.date():
            SEEN_SIGNALS.clear()
            LAST_NY_SESSION_DATE = now.date()
            log.warning("New York session reset: cleared seen signals")


def cooldown_ok(symbol):
    last = LAST_TRADE.get(symbol)
    if not last:
        return True
    return datetime.now(timezone.utc) - last > timedelta(minutes=settings.cooldown_minutes)


def fetch_symbol_data(client, symbol):
    d = {}
    d["1w"] = client.fetch_ohlcv_df(symbol, "1w", 80)
    d["1d"] = client.fetch_ohlcv_df(symbol, "1d", 160)
    d["4h"] = client.fetch_ohlcv_df(symbol, "4h", 220)
    d["1h"] = client.fetch_ohlcv_df(symbol, "1h", 260)
    d["15m"] = client.fetch_ohlcv_df(symbol, "15m", 300)
    # Build synthetic TFs to reduce MEXC requests.
    d["2h"] = client.fetch_2h_from_1h(symbol, 160)
    d["30m"] = client.fetch_30m_from_15m(symbol, 220)
    return d


def normalize_ict_signal(sig):
    sig.strategy = "ICT"
    sig.timeframe = "15m"
    risk = abs(sig.entry - sig.stop)
    sig.risk_per_unit = risk
    sig.break_even_price = sig.entry + risk * settings.break_even_r if sig.side == "buy" else sig.entry - risk * settings.break_even_r
    sig.signal_id = f"{sig.symbol}|ICT|{sig.side}|{sig.signal_time}|{round(sig.entry,8)}|{round(sig.stop,8)}"
    return sig


def build_signals_for_symbol(client, symbol):
    dfs = fetch_symbol_data(client, symbol)
    bias, bias_reason = weekly_bias(dfs["1w"], dfs["1d"])
    if bias not in ("buy", "sell"):
        log.info("No bias: %s | %s", symbol, bias_reason)
        return []
    ok4h, reason4h = four_h_spm_allows(dfs["4h"], bias)
    if not ok4h:
        log.info("SPM filter blocked %s | bias=%s | %s | %s", symbol, bias, bias_reason, reason4h)
        return []

    signals = []
    if settings.enable_dls:
        for s in detect_dls_signals(symbol, dfs):
            if s.side == bias:
                s.reason = f"{bias_reason}; {reason4h}; {s.reason}"
                signals.append(s)
            else:
                log.info("DLS signal blocked by bias: %s %s vs %s", symbol, s.side, bias)

    if settings.enable_ict:
        ict, msg = detect_ict_signal(symbol, dfs["1d"], dfs["4h"], dfs["1h"], dfs["15m"])
        if ict:
            ict = normalize_ict_signal(ict)
            if ict.side == bias:
                ict.reason = f"{bias_reason}; {reason4h}; {ict.reason}"
                signals.append(ict)
            else:
                log.info("ICT signal blocked by bias: %s %s vs %s", symbol, ict.side, bias)
        else:
            log.info("No ICT setup: %s | %s", symbol, msg)

    return signals


def main():
    log.info("Starting MASTER BOT | version=%s", VERSION)
    log.info("DryRun=%s LiveOrders=%s Risk=%s Leverage=%sx MaxOpen=%s MaxPerCycle=%s", settings.dry_run, settings.use_live_orders, settings.risk_per_trade, settings.leverage, settings.max_open_positions, settings.max_new_trades_per_cycle)
    client = MexcClient()
    trades = TradeManager()
    symbols = list(client.valid_symbols())
    log.info("Symbols=%s", tuple(symbols))

    while True:
        try:
            reset_seen_at_ny_open()
            trades.manage(client)

            if "NEWYORK" in settings.trading_sessions.upper() and not in_ny_session():
                log.info("Outside New York session - no analysis")
                time.sleep(settings.loop_seconds)
                continue

            open_count = len(client.open_positions()) + len(trades.open_trades)
            if open_count >= settings.max_open_positions:
                log.info("Max open positions reached: %s", open_count)
                time.sleep(settings.loop_seconds)
                continue

            all_signals = []
            for symbol in symbols[:settings.max_symbols_per_cycle]:
                try:
                    if not cooldown_ok(symbol):
                        log.info("Cooldown active: %s", symbol)
                        continue
                    if client.has_position(symbol):
                        log.info("Exchange position already open: %s", symbol)
                        continue
                    sigs = build_signals_for_symbol(client, symbol)
                    for sig in sigs:
                        if sig.signal_id in SEEN_SIGNALS or trades.has_signal(sig.signal_id):
                            log.info("Duplicate signal skipped: %s", sig.signal_id)
                            continue
                        all_signals.append(sig)
                except Exception as e:
                    msg = str(e)
                    log.exception("Error scanning %s: %s", symbol, e)
                    if "Requests are too frequent" in msg or "code\":510" in msg:
                        log.warning("Rate limit hit - backing off %s seconds", settings.rate_limit_backoff_seconds)
                        time.sleep(settings.rate_limit_backoff_seconds)
                time.sleep(settings.symbol_delay_seconds)

            all_signals.sort(key=lambda s: (s.score, 1 if s.strategy.startswith("DLS") else 0), reverse=True)
            slots = max(0, settings.max_open_positions - (len(client.open_positions()) + len(trades.open_trades)))
            take = min(slots, settings.max_new_trades_per_cycle, len(all_signals))
            if all_signals:
                log.warning("Signals found=%s | taking=%s", len(all_signals), take)

            for sig in all_signals[:take]:
                try:
                    amount = position_size(client, sig.symbol, sig.entry, sig.stop)
                    if amount <= 0:
                        log.warning("Invalid amount for signal: %s", sig.signal_id)
                        continue
                    log.warning("TRADE %s %s %s entry=%s sl=%s tp=%s amount=%s | %s", sig.strategy, sig.side.upper(), sig.symbol, sig.entry, sig.stop, sig.target, amount, sig.reason)
                    client.configure_futures(sig.symbol, sig.side)
                    side = "buy" if sig.side == "buy" else "sell"
                    client.create_market_order(sig.symbol, side, amount, reduce_only=False)
                    trades.add(sig, amount)
                    SEEN_SIGNALS.add(sig.signal_id)
                    LAST_TRADE[sig.symbol] = datetime.now(timezone.utc)
                except Exception as e:
                    log.exception("Order failed %s: %s", sig.symbol, e)

            stats = get_stats()
            log.info("Stats: wins=%s losses=%s breakeven=%s total=%s netR=%.2f", stats.get("wins"), stats.get("losses"), stats.get("breakeven"), stats.get("total_closed"), float(stats.get("net_r",0)))
            time.sleep(settings.loop_seconds)
        except Exception as e:
            log.exception("Main loop error: %s", e)
            time.sleep(settings.rate_limit_backoff_seconds)

if __name__ == "__main__":
    main()
