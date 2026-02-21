#!/usr/bin/env python3
"""
Backtesting Module
Replays historical kline data through the signal logic to measure strategy performance.
"""

import argparse
import importlib.util
import json
import os
import sys
import time
from datetime import datetime, timedelta
from typing import Optional, Dict, List, Any

import requests

# Load btc-scanner.py as a module (filename has hyphen, not allowed in import)
def load_btc_scanner():
    """Load btc-scanner.py as a module."""
    script_dir = os.path.dirname(os.path.abspath(__file__))
    scanner_path = os.path.join(script_dir, "btc-scanner.py")
    spec = importlib.util.spec_from_file_location("btc_scanner", scanner_path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module

_btc = load_btc_scanner()

# Import what we need from btc-scanner
compute_indicators = _btc.compute_indicators
evaluate_trade_setup = _btc.evaluate_trade_setup
load_config = _btc.load_config

from paper_trader import PaperTrader


# Binance API
BINANCE_URL = "https://api.binance.com/api/v3"
BTC_SYMBOL = "BTCUSDT"


def get_historical_klines(symbol: str, interval: str, start_time: int, end_time: int, limit: int = 1000) -> List[Dict]:
    """
    Fetch historical klines from Binance with pagination.

    Args:
        symbol: Trading pair (e.g., BTCUSDT)
        interval: Timeframe (e.g., 5m, 1h, 4h)
        start_time: Start timestamp in milliseconds
        end_time: End timestamp in milliseconds
        limit: Max candles per request (default 1000)

    Returns:
        List of candle dicts
    """
    all_candles = []
    current_start = start_time

    while current_start < end_time:
        url = f"{BINANCE_URL}/klines"
        params = {
            "symbol": symbol,
            "interval": interval,
            "startTime": current_start,
            "endTime": end_time,
            "limit": limit
        }

        try:
            resp = requests.get(url, params=params, timeout=30)
            resp.raise_for_status()
            data = resp.json()

            if not data:
                break

            for k in data:
                all_candles.append({
                    "time": k[0],
                    "open": float(k[1]),
                    "high": float(k[2]),
                    "low": float(k[3]),
                    "close": float(k[4]),
                    "volume": float(k[5])
                })

            # Move to next batch
            current_start = data[-1][0] + 1

            # Respect rate limits
            time.sleep(0.2)

            # Progress indicator
            if len(all_candles) % 5000 == 0:
                print(f"  Fetched {len(all_candles)} candles...")

        except Exception as e:
            print(f"Error fetching klines: {e}")
            break

    return all_candles


def compute_indicators_for_backtest(candles: List[Dict], config: Dict) -> Dict:
    """
    Compute indicators for backtesting (without sentiment/cv_volume).
    Uses the same compute_indicators function but stubs sentiment.
    """
    # Use the core compute_indicators function
    analysis = compute_indicators(candles, config)

    # Stub sentiment for backtesting (not available historically)
    analysis["sentiment"] = {
        "sentiment": "NEUTRAL",
        "score": 50,
        "sources": [],
        "source": "backtest (stubbed)"
    }

    # No crypto cv volume in backtest
    analysis["crypto_cv_volume"] = None
    analysis["volume_analysis"] = None

    return analysis


class BacktestResult:
    """Container for backtest results."""

    def __init__(self):
        self.total_signals = 0
        self.long_signals = 0
        self.short_signals = 0
        self.trades_taken = 0
        self.winning_trades = 0
        self.losing_trades = 0
        self.trades_list: List[Dict] = []

    def add_signal(self, action: str):
        self.total_signals += 1
        if action == "LONG":
            self.long_signals += 1
        elif action == "SHORT":
            self.short_signals += 1

    def add_trade(self, trade: Dict):
        self.trades_taken += 1
        self.trades_list.append(trade)
        if trade.get("pnl_usd", 0) > 0:
            self.winning_trades += 1
        else:
            self.losing_trades += 1

    def to_dict(self) -> Dict[str, Any]:
        total = self.trades_taken
        wins = self.winning_trades
        losses = self.losing_trades

        # Calculate stats
        win_rate = (wins / total * 100) if total > 0 else 0
        total_pnl = sum(t.get("pnl_usd", 0) for t in self.trades_list)
        total_pnl_pct = sum(t.get("pnl_pct", 0) for t in self.trades_list)

        wins_pnl = sum(t.get("pnl_usd", 0) for t in self.trades_list if t.get("pnl_usd", 0) > 0)
        losses_pnl = abs(sum(t.get("pnl_usd", 0) for t in self.trades_list if t.get("pnl_usd", 0) <= 0))
        profit_factor = wins_pnl / losses_pnl if losses_pnl > 0 else 0

        avg_win = wins_pnl / wins if wins > 0 else 0
        avg_loss = losses_pnl / losses if losses > 0 else 0

        best_trade = max((t.get("pnl_pct", 0) for t in self.trades_list), default=0)
        worst_trade = min((t.get("pnl_pct", 0) for t in self.trades_list), default=0)

        # Calculate max drawdown
        peak = 0
        max_dd = 0
        running = 10000  # Starting balance
        for t in self.trades_list:
            running += t.get("pnl_usd", 0)
            if running > peak:
                peak = running
            dd = (peak - running) / peak * 100 if peak > 0 else 0
            if dd > max_dd:
                max_dd = dd

        return {
            "total_signals": self.total_signals,
            "long_signals": self.long_signals,
            "short_signals": self.short_signals,
            "trades_taken": self.trades_taken,
            "winning_trades": wins,
            "losing_trades": losses,
            "win_rate": round(win_rate, 1),
            "total_pnl_usd": round(total_pnl, 2),
            "total_pnl_pct": round(total_pnl_pct, 2),
            "max_drawdown_pct": round(max_dd, 2),
            "profit_factor": round(profit_factor, 2),
            "avg_win_usd": round(avg_win, 2),
            "avg_loss_usd": round(avg_loss, 2),
            "best_trade_pct": round(best_trade, 2),
            "worst_trade_pct": round(worst_trade, 2),
            "trades": self.trades_list
        }

    def print_summary(self, days: int, timeframe: str, start_date: str, end_date: str):
        d = self.to_dict()

        print(f"\n{'='*60}")
        print(f"BACKTEST RESULTS — {days} days ({start_date} to {end_date})")
        print(f"Timeframe: {timeframe} | Config: btc-scanner.conf")
        print(f"{'='*60}")

        print(f"\nSignals Generated:  {d['total_signals']} ({d['long_signals']} LONG, {d['short_signals']} SHORT)")
        print(f"Trades Taken:       {d['trades_taken']}")
        print(f"Winning:            {d['winning_trades']} ({d['win_rate']:.1f}%)")
        print(f"Losing:             {d['losing_trades']} ({100 - d['win_rate']:.1f}%)")

        print(f"\nTotal P&L:          ${d['total_pnl_usd']:+,.2f} ({d['total_pnl_pct']:+.2f}%)")
        print(f"Max Drawdown:       {d['max_drawdown_pct']:.2f}%")
        print(f"Profit Factor:      {d['profit_factor']:.2f}")
        print(f"Avg Win:            ${d['avg_win_usd']:+,.2f}")
        print(f"Avg Loss:           ${d['avg_loss_usd']:,.2f}")
        print(f"Best Trade:         {d['best_trade_pct']:+.2f}%")
        print(f"Worst Trade:        {d['worst_trade_pct']:+.2f}%")

        print(f"{'='*60}\n")

    def print_trade_log(self, verbose: bool = False):
        if not verbose:
            return

        print("\nTRADE LOG:")
        print("-" * 80)
        for t in self.trades_list:
            direction = t.get("direction", "?")
            entry = t.get("entry_price", 0)
            exit_price = t.get("exit_price", 0)
            pnl = t.get("pnl_usd", 0)
            pnl_pct = t.get("pnl_pct", 0)
            reason = t.get("exit_reason", "unknown")
            entry_time = t.get("entry_time", "")
            exit_time = t.get("exit_time", "")

            emoji = "✅" if pnl > 0 else "❌"
            print(f"{emoji} {direction:5} | Entry: ${entry:,.0f} | Exit: ${exit_price:,.0f} | "
                  f"P&L: ${pnl:+,.2f} ({pnl_pct:+.2f}%) | {reason}")
            print(f"      {entry_time} → {exit_time}")
        print("-" * 80)


def run_backtest(
    days: int = 30,
    timeframe: str = "5m",
    config_path: str = "btc-scanner.conf",
    verbose: bool = False,
    paper_config: Optional[Dict] = None
) -> BacktestResult:
    """
    Run backtest on historical data.

    Args:
        days: Number of days to backtest
        timeframe: Timeframe (1m, 5m, 15m, 1h, 4h, etc.)
        config_path: Path to config file
        verbose: Print detailed trade log
        paper_config: Optional paper trading config dict

    Returns:
        BacktestResult object
    """
    # Load config - need to set CONFIG_FILE global first
    script_dir = os.path.dirname(os.path.abspath(__file__))
    config_file_path = os.path.join(script_dir, config_path)
    _btc.CONFIG_FILE = config_file_path  # Set the global before calling load_config
    config = load_config()
    config["MIN_TIMEFRAME"] = timeframe

    # Set up time range
    end_time = datetime.now()
    start_time = end_time - timedelta(days=days)

    start_ts = int(start_time.timestamp() * 1000)
    end_ts = int(end_time.timestamp() * 1000)

    print(f"\n📊 Fetching {days} days of {timeframe} historical data...")
    print(f"   Range: {start_time.strftime('%Y-%m-%d')} to {end_time.strftime('%Y-%m-%d')}")

    # Fetch historical data
    candles = get_historical_klines(BTC_SYMBOL, timeframe, start_ts, end_ts)

    if not candles:
        print("❌ No data fetched!")
        result = BacktestResult()
        return result

    print(f"   Fetched {len(candles)} candles")

    # Initialize paper trader
    if paper_config is None:
        paper_config = {
            "PAPER_STARTING_BALANCE": 10000,
            "PAPER_POSITION_SIZE_PCT": 10,
            "PAPER_MAX_POSITIONS": 1,
            "PAPER_DEFAULT_SL_PCT": 2.0,
            "PAPER_DEFAULT_TP_PCT": 4.0,
            "TRAILING_STOP_PCT": 0
        }

    pt = PaperTrader(
        starting_balance=paper_config.get("PAPER_STARTING_BALANCE", 10000),
        max_positions=paper_config.get("PAPER_MAX_POSITIONS", 1),
        position_size_pct=paper_config.get("PAPER_POSITION_SIZE_PCT", 10),
        default_sl_pct=paper_config.get("PAPER_DEFAULT_SL_PCT", 2.0),
        default_tp_pct=paper_config.get("PAPER_DEFAULT_TP_PCT", 4.0),
        trailing_stop_pct=paper_config.get("TRAILING_STOP_PCT", 0)
    )

    result = BacktestResult()

    # Cooldown settings
    cooldown_candles = int(config.get("COOLDOWN_CANDLES", 6))
    cooldown_state = {
        "cooldown_active": False,
        "cooldown_remaining": 0,
        "cooldown_direction": None
    }

    # Sliding window - use same window size as live scanner (300 candles)
    window_size = 300

    print(f"\n🔄 Running backtest with {window_size}-candle window...")
    if config.get("TREND_FILTER_ENABLED", True):
        print(f"   Trend filter: ENABLED (EMA{config.get('EMA_FAST', 50)}/EMA{config.get('EMA_SLOW', 200)})")
    if cooldown_candles > 0:
        print(f"   Cooldown: {cooldown_candles} candles after stop loss")

    # Process each candle as a potential signal point
    for i in range(window_size, len(candles)):
        # Get window of candles
        window = candles[i - window_size:i + 1]

        # Skip if not enough data
        if len(window) < window_size:
            continue

        # Get current price and time
        current_price = window[-1]["close"]
        current_time = datetime.fromtimestamp(window[-1]["time"] / 1000).strftime("%Y-%m-%d %H:%M:%S")

        # Update cooldown counter
        if cooldown_state["cooldown_active"]:
            cooldown_state["cooldown_remaining"] -= 1
            if cooldown_state["cooldown_remaining"] <= 0:
                cooldown_state["cooldown_active"] = False
                cooldown_state["cooldown_remaining"] = 0
                cooldown_state["cooldown_direction"] = None

        # Compute indicators and evaluate
        try:
            analysis = compute_indicators_for_backtest(window, config)
            trade = evaluate_trade_setup(analysis, config, cooldown_state)
        except Exception as e:
            continue

        action = trade["action"]

        # Record signal
        result.add_signal(action)

        # Update paper positions with current price
        if pt.positions:
            closed = pt.update_positions(current_price, current_time)
            for pos in closed:
                result.add_trade(pos)
                # Check if this was a stop loss - trigger cooldown
                if pos.get("exit_reason") == "stop_loss":
                    cooldown_state["cooldown_active"] = True
                    cooldown_state["cooldown_remaining"] = cooldown_candles
                    cooldown_state["cooldown_direction"] = pos["direction"]

        # Handle signal flips
        if action == "LONG" and any(p["direction"] == "SHORT" for p in pt.positions):
            pt.close_all_positions(current_price, current_time, "signal_flip")
        elif action == "SHORT" and any(p["direction"] == "LONG" for p in pt.positions):
            pt.close_all_positions(current_price, current_time, "signal_flip")

        # Open new position on signal
        if action in ("LONG", "SHORT") and pt.can_open():
            pos = pt.open_position(
                action,
                current_price,
                current_time,
                ai_rec=None,  # No AI in backtest
                confirmations=len(trade.get("confirmations", [])),
                confidence=trade["confidence"]
            )

        # Progress indicator
        if i % 5000 == 0 and i > 0:
            print(f"   Processed {i}/{len(candles)} candles...")

    # Close any remaining positions at final price
    final_price = candles[-1]["close"]
    final_time = datetime.fromtimestamp(candles[-1]["time"] / 1000).strftime("%Y-%m-%d %H:%M:%S")

    if pt.positions:
        closed = pt.update_positions(final_price, final_time, "backtest_end")
        for pos in closed:
            result.add_trade(pos)

    # Print results
    start_str = start_time.strftime("%Y-%m-%d")
    end_str = end_time.strftime("%Y-%m-%d")
    result.print_summary(days, timeframe, start_str, end_str)
    result.print_trade_log(verbose)

    # Always append to permanent backtest log
    append_backtest_log(result, days, timeframe, start_str, end_str)

    return result


def append_backtest_log(result: BacktestResult, days: int, timeframe: str, start_date: str, end_date: str, filepath: str = "backtest_log.txt"):
    """Append backtest results and trade log to a permanent log file."""
    script_dir = os.path.dirname(os.path.abspath(__file__))
    log_path = os.path.join(script_dir, filepath)

    d = result.to_dict()
    run_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    lines = []
    lines.append(f"\n{'='*80}")
    lines.append(f"BACKTEST RUN — {run_time}")
    lines.append(f"{'='*80}")
    lines.append(f"Period: {days} days ({start_date} to {end_date}) | Timeframe: {timeframe}")
    lines.append(f"Signals: {d['total_signals']} ({d['long_signals']} LONG, {d['short_signals']} SHORT)")
    lines.append(f"Trades: {d['trades_taken']} | Win: {d['winning_trades']} ({d['win_rate']:.1f}%) | Loss: {d['losing_trades']}")
    lines.append(f"P&L: ${d['total_pnl_usd']:+,.2f} ({d['total_pnl_pct']:+.2f}%)")
    lines.append(f"Max Drawdown: {d['max_drawdown_pct']:.2f}% | Profit Factor: {d['profit_factor']:.2f}")
    lines.append(f"Avg Win: ${d['avg_win_usd']:+,.2f} | Avg Loss: ${d['avg_loss_usd']:,.2f}")
    lines.append(f"Best: {d['best_trade_pct']:+.2f}% | Worst: {d['worst_trade_pct']:+.2f}%")
    lines.append(f"{'-'*80}")
    lines.append(f"TRADES:")

    for t in result.trades_list:
        direction = t.get("direction", "?")
        entry = t.get("entry_price", 0)
        exit_price = t.get("exit_price", 0)
        pnl = t.get("pnl_usd", 0)
        pnl_pct = t.get("pnl_pct", 0)
        reason = t.get("exit_reason", "unknown")
        entry_time = t.get("entry_time", "")
        exit_time = t.get("exit_time", "")
        win = "WIN " if pnl > 0 else "LOSS"

        lines.append(f"  {win} {direction:5} | ${entry:,.0f} -> ${exit_price:,.0f} | "
                      f"${pnl:+,.2f} ({pnl_pct:+.2f}%) | {reason} | {entry_time} -> {exit_time}")

    lines.append(f"{'='*80}\n")

    with open(log_path, "a", encoding="utf-8") as f:
        f.write("\n".join(lines))

    print(f"📝 Trade log appended to {filepath}")


def save_backtest_results(result: BacktestResult, filepath: str = "backtest_results.json"):
    """Save backtest results to JSON file."""
    data = result.to_dict()
    with open(filepath, "w") as f:
        json.dump(data, f, indent=2)
    print(f"💾 Results saved to {filepath}")


def main():
    parser = argparse.ArgumentParser(description="BTC Scanner Backtester")
    parser.add_argument("--days", type=int, default=30, help="Number of days to backtest (default: 30)")
    parser.add_argument("--timeframe", type=str, default="5m", help="Timeframe (default: 5m)")
    parser.add_argument("--config", type=str, default="btc-scanner.conf", help="Config file path")
    parser.add_argument("--verbose", action="store_true", help="Print detailed trade log")
    parser.add_argument("--save", type=str, help="Save results to JSON file")

    args = parser.parse_args()

    result = run_backtest(
        days=args.days,
        timeframe=args.timeframe,
        config_path=args.config,
        verbose=args.verbose
    )

    if args.save:
        save_backtest_results(result, args.save)


if __name__ == "__main__":
    main()
