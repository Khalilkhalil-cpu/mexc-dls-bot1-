import time
import traceback
from datetime import datetime
from zoneinfo import ZoneInfo

from bias_engine import final_bias, h4_spm_filter
from config import settings
from dls_engine import detect_dls
from ict_engine import detect_ict_signal
from logger import log
from mexc_client import MexcClient
from order_manager import OrderManager
from risk_manager import position_size, should_move_to_break_even, exit_reason
from trade_manager import TradeManager


BOT_VERSION = "master-ict-dls-spm-live-v1-risk3-rr3"
SEEN_SIGNALS = set()


def in_newyork_session() -> bool:
    if "NEWYORK" not in settings.trading_sessions.upper():
        return True
    now = datetime.now(ZoneInfo("America/New_York"))
    if now.hour < settings.ny_start_hour:
        return False
    if now.hour > settings.ny_end_hour:
        return False
    if now.hour == settings.ny_end_hour and now.minute > settings.ny_end_minute:
        return False
    return True


def manage_open_trades(client, trades, orders):
    for trade in list(trades.open_trades):
        try:
            price = client.last_price(trade.symbol)

            if should_move_to_break_even(trade, price):
                trade.stop_loss = trade.entry
                trade.break_even_moved = True
                log.warning("Moved SL to break-even: %s", trade.trade_id)

            reason = exit_reason(trade, price)
            if reason:
                orders.close_trade(trade, reason, price)

            time.sleep(settings.request_delay_seconds)
        except Exception as exc:
            log.error("Error managing trade %s: %s", trade.trade_id, exc)
            log.error(traceback.format_exc())


def build_symbol_signals(client, symbol):
    signals = []

    df_1w = client.fetch_closed_df(symbol, "1w", 80)
    time.sleep(settings.request_delay_seconds)
    df_1d = client.fetch_closed_df(symbol, "1d", 260)
    time.sleep(settings.request_delay_seconds)
    df_4h = client.fetch_closed_df(symbol, "4h", 1000)
    time.sleep(settings.request_delay_seconds)
    df_1h = client.fetch_closed_df(symbol, "1h", 600)
    time.sleep(settings.request_delay_seconds)
    df_2h = client.aggregate_2h_from_1h(df_1h, 300)
    df_15m = client.fetch_closed_df(symbol, "15m", 1000)
    time.sleep(settings.request_delay_seconds)

    bias = final_bias(df_1w, df_1d)
    if bias == "neutral":
        return [], "neutral bias"

    ok4h, reason4h = h4_spm_filter(df_4h, bias)
    if not ok4h:
        return [], reason4h

    if settings.enable_dls:
        for tf, df in (("1h", df_1h), ("2h", df_2h)):
            if tf not in settings.dls_tf_list:
                continue
            sig = detect_dls(symbol, df, tf)
            if sig and sig.side == bias:
                signals.append(sig)

    if settings.enable_ict:
        sig = detect_ict_signal(symbol, bias, df_4h, df_1h, df_15m)
        if sig:
            signals.append(sig)

    return signals, f"bias={bias}; {reason4h}"


def main():
    log.info("Starting MASTER BOT | version=%s", BOT_VERSION)
    log.info("DryRun=%s LiveOrders=%s Risk=%s RR=%s Leverage=%sx MaxOpen=%s MaxPerCycle=%s",
             settings.dry_run, settings.use_live_orders, settings.risk_per_trade, settings.rr_target,
             settings.leverage, settings.max_open_positions, settings.max_new_trades_per_cycle)

    client = MexcClient()
    trades = TradeManager()
    orders = OrderManager(client, trades)

    while True:
        try:
            manage_open_trades(client, trades, orders)

            if not in_newyork_session():
                log.info("Outside New York session - no analysis")
                time.sleep(settings.loop_seconds)
                continue

            opened_this_cycle = 0

            for symbol in settings.symbol_list:
                if len(trades.open_trades) >= settings.max_open_positions:
                    break
                if opened_this_cycle >= settings.max_new_trades_per_cycle:
                    break
                if trades.has_open_symbol(symbol):
                    continue

                try:
                    signals, reason = build_symbol_signals(client, symbol)
                    if not signals:
                        log.info("No setup %s | %s", symbol, reason)
                        time.sleep(settings.symbol_delay_seconds)
                        continue

                    for signal in signals:
                        if signal.signal_id in SEEN_SIGNALS:
                            continue
                        SEEN_SIGNALS.add(signal.signal_id)

                        amount = position_size(client, signal)
                        if amount <= 0:
                            log.warning("Invalid amount for %s", signal.signal_id)
                            continue

                        log.warning("SIGNAL %s %s %s entry=%s sl=%s tp=%s amount=%s reason=%s",
                                    signal.strategy, signal.side.upper(), signal.symbol,
                                    signal.entry, signal.stop_loss, signal.take_profit, amount, signal.reason)

                        orders.open_signal(signal, amount)
                        opened_this_cycle += 1
                        break

                    time.sleep(settings.symbol_delay_seconds)

                except Exception as exc:
                    log.error("Error scanning %s: %s", symbol, exc)
                    log.error(traceback.format_exc())
                    time.sleep(settings.rate_limit_backoff_seconds)

            time.sleep(settings.loop_seconds)

        except Exception as exc:
            log.error("Main loop error: %s", exc)
            log.error(traceback.format_exc())
            time.sleep(settings.rate_limit_backoff_seconds)


if __name__ == "__main__":
    main()
