# Master ICT + DLS Bot v2 Backtest

This version includes the rules currently defined in the conversation:

## Bias / SPM

1. Weekly bias first:
   - Last closed weekly candle closes above previous weekly high = BUY ONLY.
   - Last closed weekly candle closes below previous weekly low = SELL ONLY.
   - If weekly is inside previous week range, use Daily SPM fallback.
   - If no Daily SELL SPM exists, keep BUY ONLY as taught.

2. 4H SPM alignment:
   - If bias is BUY, latest confirmed 4H SPM must be BUY.
   - If latest 4H SPM is SELL, bot skips trading until a new 4H BUY SPM appears.
   - Opposite for SELL.

3. SPM Engine:
   - BUY SPM: Candle 2 is the lowest candle.
   - SELL SPM: Candle 2 is the highest candle.
   - Candle 1 is found by moving left one candle at a time.
   - Candle 1 cannot be an inside candle.
   - BUY: Candle 2 must not take Candle 1 low and must not close below Candle 1 body.
   - SELL: Candle 2 must not take Candle 1 high and must not close above Candle 1 body.
   - BUY SPM confirms when a later candle closes above Candle 1 high.
   - SELL SPM confirms when a later candle closes below Candle 1 low.

## DLS Models

### DLS Type 1
Original bot model. It enters immediately after Candle 3 closes.

### DLS Type 2
- DLS happens, but Candle 3 does not close beyond Candle 2 open.
- 1H Type 2 needs 15M EC + 15M SPM confirmation.
- 2H Type 2 needs 30M EC + 30M SPM confirmation.
- Stop is beyond both original DLS Candle 3 and the lower-timeframe SPM Candle 2.

## EC Candle

Current coded definition:
- BUY EC: candle sweeps previous candle low, closes bullish, and closes above previous candle open.
- SELL EC: candle sweeps previous candle high, closes bearish, and closes below previous candle open.

## Backtest

Run locally or on Railway shell:

```bash
python backtest.py
```

Backtest output files:

```text
logs/backtest_summary.json
logs/backtest_results.csv
```

The summary prints:
- how many trades were found
- how many closed
- wins
- losses
- breakeven
- win rate
- net R

## Recommended Railway variables for backtest

```text
DRY_RUN=true
USE_LIVE_ORDERS=false
BACKTEST_DAYS=30
BACKTEST_SYMBOLS=BTC/USDT:USDT,ETH/USDT:USDT,SOL/USDT:USDT,XRP/USDT:USDT,BNB/USDT:USDT
BACKTEST_RISK_PER_TRADE=0.02
BACKTEST_MAX_OPEN_POSITIONS=2
BACKTEST_MAX_NEW_TRADES_PER_BAR=1
ENABLE_ICT=true
ENABLE_DLS=true
ENABLE_DLS_TYPE1=true
ENABLE_DLS_TYPE2=true
```

## Important

This version is for backtesting first. Do not switch live until the backtest results are acceptable and the chart examples match your manual rules.
