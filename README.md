# BTC Scanner

A confluence-based Bitcoin trading signal engine with sentiment analysis, paper trading, backtesting, and AI-powered strategy optimization.

## What It Does

Monitors BTC/USDT on Binance and fires LONG/SHORT signals when multiple technical indicators align. Uses 6 independent confirmation layers — no single indicator trades alone.

**Signal confirmations:**
1. RSI — Oversold / Overbought
2. MACD — Bullish / Bearish crossover
3. Support & Resistance — Price at key level with volume-confirmed touches
4. Fibonacci — Price at retracement zone (0.382 / 0.5 / 0.618 / 0.786)
5. Volume Profile — Price at High Volume Node (HVN)
6. EMA Trend Filter — EMA50/EMA200 gates out counter-trend trades

**Minimum 3 of 6 confirmations required to trigger a signal.**

## Backtest Results

Tested on 240 days of 1h BTC data (Jun 2025 – Feb 2026, bull + bear market):

| Metric | Result |
|--------|--------|
| Trades | 73 |
| Win Rate | 42.5% |
| Total P&L | +41.02% |
| Profit Factor | 1.43 |
| Max Drawdown | 1.45% |
| Avg Win | +$44.47 |
| Avg Loss | -$23.02 |

## Files

| File | Purpose |
|------|---------|
| `btc-scanner.py` | Main scanner — live signals, Telegram alerts, DeepSeek AI trade levels |
| `backtester.py` | Historical replay against Binance kline data |
| `paper_trader.py` | Virtual portfolio with TP/SL/trailing stop, persists to JSON |
| `data_sources.py` | Sentiment — Fear&Greed Index, Reddit, CoinGecko, Google Trends |
| `optimizer.py` | MiniMax M2.5 AI strategy optimizer *(coming soon)* |
| `btc-scanner.conf` | Your local config with API keys *(gitignored)* |
| `btc-scanner.conf.example` | Config template — copy this to get started |

## Setup

**1. Clone and install dependencies**
```bash
git clone https://github.com/DgenKing/BTC-Scanner.git
cd BTC-Scanner
pip install requests pytrends
```

**2. Create your config**
```bash
cp btc-scanner.conf.example btc-scanner.conf
```
Then edit `btc-scanner.conf` and fill in your API keys:
- `DEEPSEEK_API_KEY` — from [platform.deepseek.com](https://platform.deepseek.com) (free tier available)
- `MINIMAX_API_KEY` — from [platform.minimax.io](https://platform.minimax.io)
- Telegram bot token and chat ID (optional — for alerts)

**3. Run the scanner**
```bash
python3 btc-scanner.py
```

## Backtesting

Replay historical data through the same signal logic:

```bash
# 120 days, 1h timeframe (recommended)
python3 backtester.py --days 120 --timeframe 1h

# 240 days with full trade log
python3 backtester.py --days 240 --timeframe 1h --verbose

# Save results to JSON
python3 backtester.py --days 120 --timeframe 1h --save results.json
```

All backtest runs are permanently appended to `backtest_log.txt`.

## Paper Trading

Enable virtual trading alongside live signals by editing `btc-scanner.conf`:

```ini
[PAPER_TRADING]
PAPER_TRADING_ENABLED = True
PAPER_STARTING_BALANCE = 10000
PAPER_POSITION_SIZE_PCT = 10    # 10% of balance per trade
PAPER_DEFAULT_SL_PCT = 2.0      # 2% stop loss
PAPER_DEFAULT_TP_PCT = 4.0      # 4% take profit
```

Paper trading state persists across restarts in `paper_trades.json`.

## Key Config Settings

```ini
[SETTINGS]
MIN_TIMEFRAME = 1h              # Recommended: 1h

[TREND_FILTER]
TREND_FILTER_ENABLED = True     # Blocks counter-trend trades
EMA_FAST = 50
EMA_SLOW = 200
COOLDOWN_CANDLES = 6            # Wait after stop loss before re-entering

[CONFLUENCE]
MIN_CONFIRMATIONS = 3           # Out of 6 required to signal
```

## Strategy Optimization

The optimizer uses MiniMax M2.5 to analyze backtest results and generate improved parameter configurations. Each suggestion is written to a separate test config and backtested automatically.

```bash
# Run 3 optimization iterations
python3 optimizer.py --iterations 3 --days 120 --timeframe 1h
```

The optimizer never modifies your live `btc-scanner.conf` — all test configs are written to `btc-scanner_test1.conf`, `btc-scanner_test2.conf`, etc.

## Data Sources (all free, no auth required)

| Source | Data | Update Frequency |
|--------|------|-----------------|
| Binance API | Price / OHLCV | Real-time |
| alternative.me | Fear & Greed Index | Daily |
| Reddit r/Bitcoin | Post sentiment | 15 min |
| CoinGecko | Price change % | 5 min |
| Google Trends | Search interest | Hourly |

## License

MIT
