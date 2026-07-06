from pydantic_settings import BaseSettings
from typing import Tuple


class Settings(BaseSettings):
    mexc_api_key: str = ""
    mexc_secret: str = ""

    dry_run: bool = True
    use_live_orders: bool = False

    symbols: str = "BTC/USDT:USDT,ETH/USDT:USDT,SOL/USDT:USDT,XRP/USDT:USDT,BNB/USDT:USDT,DOGE/USDT:USDT,ADA/USDT:USDT,AVAX/USDT:USDT,LINK/USDT:USDT,LTC/USDT:USDT,BCH/USDT:USDT,DOT/USDT:USDT,UNI/USDT:USDT,APT/USDT:USDT,ARB/USDT:USDT,OP/USDT:USDT,NEAR/USDT:USDT,ATOM/USDT:USDT"
    backtest_symbols: str = "BTC/USDT:USDT,ETH/USDT:USDT,SOL/USDT:USDT,XRP/USDT:USDT,BNB/USDT:USDT"

    enable_ict: bool = True
    enable_dls: bool = True
    enable_dls_type1: bool = True
    enable_dls_type2: bool = True

    # User requested risk 3% and 3R win target.
    risk_per_trade: float = 0.03
    risk_reward: float = 3.0
    rr_target: float = 3.0
    break_even_r: float = 0.82

    leverage: int = 50
    margin_mode: str = "isolated"
    min_notional_usdt: float = 5.0

    max_open_positions: int = 2
    max_new_trades_per_cycle: int = 1
    max_daily_losses: int = 3
    max_daily_loss_r: float = 9.0

    trading_sessions: str = "NEWYORK"
    ny_start_hour: int = 2
    ny_end_hour: int = 11
    ny_end_minute: int = 30

    loop_seconds: int = 120
    request_delay_seconds: float = 0.5
    symbol_delay_seconds: float = 1.0
    rate_limit_backoff_seconds: int = 60
    price_cache_seconds: int = 20

    # SPM settings
    spm_search_back: int = 160
    spm_candle1_search_back: int = 50

    # DLS settings
    dls_timeframes: str = "1h,2h"
    dls_max_extra_candles: int = 3
    max_type2_confirmation_bars: int = 80

    # Backtest
    backtest_days: int = 30
    backtest_start_balance: float = 1000.0
    backtest_result_file: str = "logs/backtest_results.csv"
    backtest_summary_file: str = "logs/backtest_summary.json"

    class Config:
        env_file = ".env"
        case_sensitive = False

    @property
    def symbol_list(self) -> Tuple[str, ...]:
        return tuple(s.strip() for s in self.symbols.split(",") if s.strip())

    @property
    def backtest_symbol_list(self) -> Tuple[str, ...]:
        return tuple(s.strip() for s in self.backtest_symbols.split(",") if s.strip())

    @property
    def dls_tf_list(self) -> Tuple[str, ...]:
        return tuple(t.strip().lower() for t in self.dls_timeframes.split(",") if t.strip())


settings = Settings()
