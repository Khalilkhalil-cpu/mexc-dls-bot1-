# Run the 1 Month Backtest

## Railway
Set the service start command to:

```bash
python backtest.py
```

Then redeploy. The result prints in Railway logs and saves:

```text
logs/backtest_summary.json
logs/backtest_results.csv
```

## Important variables

```env
DRY_RUN=true
USE_LIVE_ORDERS=false
BACKTEST_DAYS=30
BACKTEST_SYMBOLS=BTC/USDT:USDT,ETH/USDT:USDT,SOL/USDT:USDT,XRP/USDT:USDT,BNB/USDT:USDT
BACKTEST_USE_NEWYORK_SESSION=true
BACKTEST_RISK_PER_TRADE=0.02
BACKTEST_START_BALANCE=1000
```

The backtester fetches MEXC historical candles and replays them candle-by-candle. It reports total trades, wins, losses, breakeven, open at end, win rate, and net R.
