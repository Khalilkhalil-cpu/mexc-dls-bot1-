import time
from typing import Dict, Tuple

import ccxt
import pandas as pd

from config import settings
from logger import log


class MexcClient:
    def __init__(self):
        self.exchange = ccxt.mexc({
            "apiKey": settings.mexc_api_key,
            "secret": settings.mexc_secret,
            "enableRateLimit": True,
            "options": {"defaultType": "swap"},
        })
        self._price_cache: Dict[str, Tuple[float, float]] = {}

        self.exchange.load_markets()
        log.info("MEXC markets loaded")

        self.configure_futures()

    def configure_futures(self):
        if settings.dry_run:
            log.info("DRY_RUN futures config: margin=%s leverage=%sx", settings.margin_mode, settings.leverage)
            return

        open_type = 1 if settings.margin_mode.lower().startswith("isol") else 2
        for symbol in settings.symbol_list:
            for position_type in (1, 2):
                try:
                    self.exchange.set_leverage(settings.leverage, symbol, {
                        "openType": open_type,
                        "positionType": position_type,
                    })
                    log.info("Set leverage %s positionType=%s leverage=%sx", symbol, position_type, settings.leverage)
                    time.sleep(settings.request_delay_seconds)
                except Exception as exc:
                    log.warning("Could not set leverage for %s positionType=%s: %s", symbol, position_type, exc)

    def fetch_ohlcv_df(self, symbol: str, timeframe: str, limit: int = 500) -> pd.DataFrame:
        raw = self.exchange.fetch_ohlcv(symbol, timeframe=timeframe, limit=limit)
        df = pd.DataFrame(raw, columns=["timestamp", "open", "high", "low", "close", "volume"])
        df["datetime"] = pd.to_datetime(df["timestamp"], unit="ms", utc=True)
        return df.sort_values("timestamp").drop_duplicates("timestamp").reset_index(drop=True)

    def aggregate_2h_from_1h(self, df_1h: pd.DataFrame, limit: int = 500) -> pd.DataFrame:
        df = df_1h.copy()
        df["datetime"] = pd.to_datetime(df["datetime"], utc=True)
        df = df.set_index("datetime")
        agg = df.resample("2h", label="left", closed="left").agg({
            "timestamp": "first",
            "open": "first",
            "high": "max",
            "low": "min",
            "close": "last",
            "volume": "sum",
        }).dropna()
        agg = agg.reset_index()
        agg["timestamp"] = (agg["datetime"].astype("int64") // 1_000_000).astype("int64")
        return agg.tail(limit).reset_index(drop=True)

    def fetch_closed_df(self, symbol: str, timeframe: str, limit: int = 500) -> pd.DataFrame:
        tf = timeframe.lower()
        if tf == "2h":
            df_1h = self.fetch_ohlcv_df(symbol, "1h", limit=(limit * 2) + 20)
            return self.aggregate_2h_from_1h(df_1h, limit=limit)
        df = self.fetch_ohlcv_df(symbol, tf, limit=limit)
        return df.tail(limit).reset_index(drop=True)

    def last_price(self, symbol: str) -> float:
        now = time.time()
        cached = self._price_cache.get(symbol)
        if cached and now - cached[0] <= settings.price_cache_seconds:
            return cached[1]
        ticker = self.exchange.fetch_ticker(symbol)
        price = float(ticker["last"])
        self._price_cache[symbol] = (now, price)
        return price

    def balance_usdt(self) -> float:
        if settings.dry_run or not settings.use_live_orders:
            return 1000.0
        bal = self.exchange.fetch_balance()
        for key in ("USDT", "usdt"):
            if key in bal.get("total", {}):
                return float(bal["total"][key])
        return 0.0

    def amount_to_precision(self, symbol: str, amount: float) -> float:
        return float(self.exchange.amount_to_precision(symbol, amount))

    def price_to_precision(self, symbol: str, price: float) -> float:
        return float(self.exchange.price_to_precision(symbol, price))

    def contracts_from_risk(self, symbol: str, entry: float, stop: float, risk_amount_usdt: float) -> float:
        risk_per_unit = abs(entry - stop)
        if risk_per_unit <= 0:
            return 0.0
        raw_amount = risk_amount_usdt / risk_per_unit
        amount = float(self.exchange.amount_to_precision(symbol, raw_amount))
        return max(amount, 0.0)

    def create_market_order(self, symbol: str, side: str, amount: float, reduce_only: bool = False):
        params = {"reduceOnly": reduce_only} if reduce_only else {}
        if settings.dry_run or not settings.use_live_orders:
            log.info("DRY_RUN order: %s %s amount=%s reduceOnly=%s", side.upper(), symbol, amount, reduce_only)
            return {"id": "dry_run", "symbol": symbol, "side": side, "amount": amount}

        return self.exchange.create_order(symbol, "market", side, amount, None, params)
