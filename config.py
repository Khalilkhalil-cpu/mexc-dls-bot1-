from pydantic_settings import BaseSettings
from typing import Tuple

class Settings(BaseSettings):
    mexc_api_key: str = ""
    mexc_secret: str = ""

    dry_run: bool = True
    use_live_orders: bool = False

    symbols: str = "BTC/USDT:USDT,ETH/USDT:USDT,SOL/USDT:USDT,XRP/USDT:USDT,BNB/USDT:USDT,DOGE/USDT:USDT,ADA/USDT:USDT,AVAX/USDT:USDT,LINK/USDT:USDT,LTC/USDT:USDT,BCH/USDT:USDT,DOT/USDT:USDT,TRX/USDT:USDT,UNI/USDT:USDT,APT/USDT:USDT,ARB/USDT:USDT,OP/USDT:USDT,NEAR/USDT:USDT,ATOM/USDT:USDT"
    scan_all_usdt_swaps: bool = False
    max_symbols: int = 20
    max_symbols_per_cycle: int = 8

    enable_ict: bool = True
    enable_dls: bool = True
    enable_dls_type1: bool = True
    enable_dls_type2: bool = True

    dls_timeframes: str = "1h,2h"
    risk_reward: float =3
    break_even_r: float = 0.82
    ict_min_score: int = 85
    ict_entry_mode: str = "pb"
    min_score: int = 85
    entry_mode: str = "pb"
    rr_target: float = 3

    # Risk. User requested 2% for live testing after backtest.
    sizing_mode: str = "risk_percent"
    risk_per_trade: float = 0.02
    trade_margin_usdt: float = 1.0
    leverage: int = 50
    margin_mode: str = "isolated"
    min_notional_usdt: float = 5.0

    max_open_positions: int = 2
    max_new_trades_per_cycle: int = 1
    max_daily_losses: int = 3
    max_daily_loss_r: float = 6.0
    cooldown_minutes: int = 180

    trading_sessions: str = "NEWYORK"
    ny_start_hour: int = 2
    ny_end_hour: int = 11
    ny_end_minute: int = 30

    loop_seconds: int = 120
    request_delay_seconds: float = 0.5
    symbol_delay_seconds: float = 1.0
    rate_limit_backoff_seconds: int = 60
    price_cache_seconds: int = 20

    # SPM settings learned from you.
    spm_search_back: int = 100
    spm_candle1_search_back: int = 40
    dls_type2_lookback: int = 12
    max_type2_confirmation_bars: int = 80

    # Backtest settings
    backtest_days: int = 30
    backtest_symbols: str = "BTC/USDT:USDT,ETH/USDT:USDT,SOL/USDT:USDT,XRP/USDT:USDT,BNB/USDT:USDT"
    backtest_start_balance: float = 1000.0
    backtest_risk_per_trade: float = 0.02
    backtest_max_open_positions: int = 2
    backtest_max_new_trades_per_bar: int = 1
    backtest_max_hold_bars_15m: int = 672  # 7 days
    backtest_use_newyork_session: bool = True
    backtest_step_minutes: int = 15
    backtest_result_file: str = "logs/backtest_results.csv"
    backtest_summary_file: str = "logs/backtest_summary.json"

    class Config:
        env_file = ".env"
        case_sensitive = False

    @property
    def symbol_list(self) -> Tuple[str, ...]:
        return tuple(s.strip() for s in self.symbols.split(",") if s.strip())

    @property
    def dls_tf_list(self) -> Tuple[str, ...]:
        return tuple(t.strip().lower() for t in self.dls_timeframes.split(",") if t.strip())

    @property
    def backtest_symbol_list(self) -> Tuple[str, ...]:
        return tuple(s.strip() for s in self.backtest_symbols.split(",") if s.strip())

settings = Settings()
