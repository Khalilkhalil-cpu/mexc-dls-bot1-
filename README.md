# Master ICT + DLS + SPM Bot

Includes:
- Weekly bias
- Daily SPM fallback
- 4H SPM alignment filter
- DLS Type 1 extended
- DLS Type 2 extended
- ICT 4H FVG + 1H stop hunt + 15M CISD
- Risk per trade = 3%
- Target = 3R
- Break-even = 0.82R
- MEXC futures execution

## Railway live variables

DRY_RUN=true is default for safety.

To go live, set:

```env
DRY_RUN=false
USE_LIVE_ORDERS=true
MEXC_API_KEY=your_key
MEXC_SECRET=your_secret
RISK_PER_TRADE=0.03
RR_TARGET=3
RISK_REWARD=3
LEVERAGE=50
MARGIN_MODE=isolated
TRADING_SESSIONS=NEWYORK
MAX_OPEN_POSITIONS=2
MAX_NEW_TRADES_PER_CYCLE=1
```

## Backtest

Temporarily change Procfile to:

```text
worker: python backtest.py
```

or run:

```bash
python backtest.py
```

Results:
- logs/backtest_summary.json
- logs/backtest_results.csv
