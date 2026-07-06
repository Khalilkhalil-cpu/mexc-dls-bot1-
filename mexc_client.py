from __future__ import annotations
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
        self.exchange.load_markets()
        self._ohlcv_cache: Dict[Tuple[str, str, int], Tuple[float, pd.DataFrame]] = {}
        self._price_cache: Dict[str, Tuple[float, float]] = {}
        log.info("MEXC markets loaded")

    def valid_symbols(self):
        markets = self.exchange.markets
        if settings.scan_all_usdt_swaps:
            syms = [s for s, m in markets.items() if m.get("swap") and m.get("quote") == "USDT" and m.get("active", True)]
            return tuple(syms[:settings.max_symbols])
        return tuple(s for s in settings.symbol_list if s in markets)

    def _sleep_req(self):
        time.sleep(float(settings.request_delay_seconds))

    def fetch_ohlcv_df(self, symbol: str, timeframe: str, limit: int = 300) -> pd.DataFrame:
        key = (symbol, timeframe, limit)
        now = time.time()
        cached = self._ohlcv_cache.get(key)
        # Cache OHLCV within one loop for rate-limit protection.
        if cached and now - cached[0] < max(5, settings.price_cache_seconds):
            return cached[1].copy()
        self._sleep_req()
        raw = self.exchange.fetch_ohlcv(symbol, timeframe=timeframe, limit=limit)
        df = pd.DataFrame(raw, columns=["timestamp", "open", "high", "low", "close", "volume"])
        df["datetime"] = pd.to_datetime(df["timestamp"], unit="ms", utc=True)
        # Remove currently forming candle.
        if len(df) > 1:
            df = df.iloc[:-1].copy()
        self._ohlcv_cache[key] = (now, df)
        return df.copy()

    def fetch_2h_from_1h(self, symbol: str, limit: int = 150) -> pd.DataFrame:
        df = self.fetch_ohlcv_df(symbol, "1h", limit * 2 + 20)
        x = df.copy().set_index("datetime")
        agg = x.resample("2h", label="left", closed="left").agg({
            "open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum", "timestamp": "first"
        }).dropna().reset_index()
        return agg[["timestamp", "open", "high", "low", "close", "volume", "datetime"]].tail(limit).reset_index(drop=True)

    def fetch_30m_from_15m(self, symbol: str, limit: int = 200) -> pd.DataFrame:
        df = self.fetch_ohlcv_df(symbol, "15m", limit * 2 + 20)
        x = df.copy().set_index("datetime")
        agg = x.resample("30min", label="left", closed="left").agg({
            "open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum", "timestamp": "first"
        }).dropna().reset_index()
        return agg[["timestamp", "open", "high", "low", "close", "volume", "datetime"]].tail(limit).reset_index(drop=True)

    def balance_usdt(self) -> float:
        if settings.dry_run or not settings.use_live_orders:
            return 1000.0
        self._sleep_req()
        bal = self.exchange.fetch_balance()
        total = bal.get("USDT", {})
        return float(total.get("free", 0) or total.get("total", 0) or bal.get("total", {}).get("USDT", 0) or 0)

    def amount_to_precision(self, symbol: str, amount: float) -> float:
        return float(self.exchange.amount_to_precision(symbol, amount))

    def price_to_precision(self, symbol: str, price: float) -> float:
        return float(self.exchange.price_to_precision(symbol, price))

    def contract_size(self, symbol: str) -> float:
        market = self.exchange.market(symbol)
        return float(market.get("contractSize") or 1.0)

    def market_price(self, symbol: str) -> float:
        now = time.time()
        cached = self._price_cache.get(symbol)
        if cached and now - cached[0] <= settings.price_cache_seconds:
            return cached[1]
        self._sleep_req()
        ticker = self.exchange.fetch_ticker(symbol)
        price = float(ticker.get("last") or ticker.get("close"))
        self._price_cache[symbol] = (now, price)
        return price

    def open_positions(self):
        if settings.dry_run or not settings.use_live_orders:
            return []
        try:
            self._sleep_req()
            positions = self.exchange.fetch_positions()
        except Exception:
            return []
        result = []
        for p in positions:
            contracts = float(p.get("contracts") or p.get("info", {}).get("holdVol") or 0)
            if contracts > 0:
                result.append(p)
        return result

    def has_position(self, symbol: str) -> bool:
        for p in self.open_positions():
            if p.get("symbol") == symbol:
                return True
        return False

    def configure_futures(self, symbol: str, side: str):
        if settings.dry_run or not settings.use_live_orders:
            return
        position_type = 1 if side == "buy" else 2
        open_type = 1 if settings.margin_mode.lower().startswith("isol") else 2
        try:
            self._sleep_req()
            self.exchange.set_leverage(settings.leverage, symbol, {"openType": open_type, "positionType": position_type})
        except Exception as e:
            log.warning("set_leverage failed %s: %s", symbol, e)
        try:
            self._sleep_req()
            self.exchange.set_margin_mode(settings.margin_mode, symbol, {"openType": open_type, "positionType": position_type, "leverage": settings.leverage})
        except Exception as e:
            log.warning("set_margin_mode failed %s: %s", symbol, e)

    def create_market_order(self, symbol: str, side: str, amount: float, reduce_only: bool = False):
        if settings.dry_run or not settings.use_live_orders:
            log.info("DRY_RUN order: %s %s amount=%s reduce_only=%s", side.upper(), symbol, amount, reduce_only)
            return {"id": "dry_run", "symbol": symbol, "side": side, "amount": amount, "dry_run": True}
        params = {"reduceOnly": reduce_only} if reduce_only else {"openType": 1 if settings.margin_mode.lower().startswith("isol") else 2, "leverage": settings.leverage}
        self._sleep_req()
        return self.exchange.create_order(symbol, "market", side, amount, None, params)
