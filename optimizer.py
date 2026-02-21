#!/usr/bin/env python3
"""
MiniMax M2.5 Strategy Optimizer
Analyzes backtest results, suggests improved parameters, and tests them automatically.
"""

import argparse
import json
import os
import re
import sys
import time
from datetime import datetime

import requests

# Add parent dir to path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# Constants
MINIMAX_BASE_URL = "https://api.minimax.io/v1"
BACKTEST_LOG_FILE = "backtest_log.txt"
OPTIMIZER_LOG_FILE = "optimizer_log.txt"


def load_config(config_path):
    """Load config from file."""
    config = {}
    if os.path.exists(config_path):
        with open(config_path, "r") as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    key, value = line.split("=", 1)
                    key = key.strip()
                    value = value.strip()

                    # Remove comments
                    if "#" in value:
                        value = value.split("#")[0].strip()

                    # Parse value type
                    if value.startswith('"') and value.endswith('"'):
                        value = value[1:-1]
                    elif value.startswith("'") and value.endswith("'"):
                        value = value[1:-1]
                    elif value == "True":
                        value = True
                    elif value == "False":
                        value = False
                    elif value.isdigit():
                        value = int(value)
                    else:
                        try:
                            value = float(value)
                        except:
                            pass

                    config[key] = value
    return config


def save_config(config, filepath):
    """Save config to file, preserving structure."""
    # Read original config to preserve comments and section headers
    if os.path.exists("btc-scanner.conf"):
        with open("btc-scanner.conf", "r") as f:
            original_lines = f.readlines()
    else:
        original_lines = []

    # Write new config
    with open(filepath, "w") as f:
        # Write all original lines but update values
        current_section = ""
        for line in original_lines:
            stripped = line.strip()
            if stripped.startswith("[") and stripped.endswith("]"):
                current_section = stripped
                f.write(line)
            elif "=" in stripped and not stripped.startswith("#"):
                key = stripped.split("=")[0].strip()
                if key in config:
                    # Write with new value
                    f.write(f"{key} = {config[key]}\n")
                else:
                    f.write(line)
            else:
                f.write(line)

        # Add any new keys that weren't in original
        for key, value in config.items():
            if key not in [l.split("=")[0].strip() for l in original_lines if "=" in l]:
                f.write(f"{key} = {value}\n")


def call_minimax(api_key, system_prompt, user_prompt):
    """Call MiniMax API."""
    if not api_key or api_key == "your-minimax-key-here":
        print("⚠️  No MiniMax API key configured. Using mock optimizer.")
        return None

    try:
        resp = requests.post(
            f"{MINIMAX_BASE_URL}/chat/completions",
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json"
            },
            json={
                "model": "MiniMax-M2.5",
                "messages": [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt}
                ],
                "temperature": 0.7,
                "reasoning_split": True
            },
            timeout=120
        )
        resp.raise_for_status()
        return resp.json()["choices"][0]["message"]["content"]
    except Exception as e:
        print(f"⚠️  MiniMax API error: {e}")
        return None


def parse_minimax_response(response):
    """Parse MiniMax JSON response to extract parameter suggestions."""
    if not response:
        return None, "No response"

    # Try to find JSON block in response
    json_match = re.search(r'\{[^{}]*\}', response, re.DOTALL)
    if json_match:
        try:
            suggestions = json.loads(json_match.group())
            reasoning = response.replace(json_match.group(), "").strip()
            return suggestions, reasoning
        except:
            pass

    # Try to extract individual parameters
    params = {}

    patterns = {
        "RSI_OVERSOLD": r"RSI_OVERSOLD\s*[=:]\s*(\d+)",
        "RSI_OVERBOUGHT": r"RSI_OVERBOUGHT\s*[=:]\s*(\d+)",
        "RSI_PERIOD": r"RSI_PERIOD\s*[=:]\s*(\d+)",
        "RSI_LOOKBACK": r"RSI_LOOKBACK\s*[=:]\s*(\d+)",
        "MACD_FAST": r"MACD_FAST\s*[=:]\s*(\d+)",
        "MACD_SLOW": r"MACD_SLOW\s*[=:]\s*(\d+)",
        "MACD_SIGNAL": r"MACD_SIGNAL\s*[=:]\s*(\d+)",
        "MACD_CROSS_LOOKBACK": r"MACD_CROSS_LOOKBACK\s*[=:]\s*(\d+)",
        "EMA_FAST": r"EMA_FAST\s*[=:]\s*(\d+)",
        "EMA_SLOW": r"EMA_SLOW\s*[=:]\s*(\d+)",
        "COOLDOWN_CANDLES": r"COOLDOWN_CANDLES\s*[=:]\s*(\d+)",
        "MIN_CONFIRMATIONS": r"MIN_CONFIRMATIONS\s*[=:]\s*(\d+)",
        "MIN_SR_TOUCHES": r"MIN_SR_TOUCHES\s*[=:]\s*(\d+)",
        "SR_TOLERANCE_PCT": r"SR_TOLERANCE_PCT\s*[=:]\s*([\d.]+)",
        "FIB_TOLERANCE_PCT": r"FIB_TOLERANCE_PCT\s*[=:]\s*([\d.]+)",
        "HVN_MULTIPLIER": r"HVN_MULTIPLIER\s*[=:]\s*([\d.]+)",
        "HVN_TOLERANCE_PCT": r"HVN_TOLERANCE_PCT\s*[=:]\s*([\d.]+)",
        "VP_LOOKBACK": r"VP_LOOKBACK\s*[=:]\s*(\d+)",
        "PAPER_DEFAULT_SL_PCT": r"PAPER_DEFAULT_SL_PCT\s*[=:]\s*([\d.]+)",
        "PAPER_DEFAULT_TP_PCT": r"PAPER_DEFAULT_TP_PCT\s*[=:]\s*([\d.]+)",
    }

    for param, pattern in patterns.items():
        match = re.search(pattern, response, re.IGNORECASE)
        if match:
            value = match.group(1)
            params[param] = int(float(value)) if "." not in value else float(value)

    if params:
        # Extract reasoning (everything that's not the JSON)
        reasoning = re.sub(r'\{[^{}]*\}', '', response, flags=re.DOTALL).strip()
        reasoning = re.sub(r'\n\s*\n', '\n', reasoning).strip()
        return params, reasoning[:500] if reasoning else "No reasoning provided"

    return None, "Could not parse response"


def get_recent_backtests(n=10):
    """Get recent backtest results from log."""
    if not os.path.exists(BACKTEST_LOG_FILE):
        return []

    results = []
    with open(BACKTEST_LOG_FILE, "r") as f:
        content = f.read()

    # Parse backtest result blocks
    blocks = content.split("=" * 60)
    for block in blocks[-n:]:
        if "BACKTEST RESULTS" in block and "Timeframe:" in block:
            try:
                # Extract key metrics
                lines = block.split("\n")
                result = {}

                for line in lines:
                    if "Trades Taken:" in line:
                        result["trades"] = int(line.split(":")[-1].strip())
                    elif "Win Rate:" in line:
                        result["win_rate"] = float(line.split(":")[-1].strip().replace("%", ""))
                    elif "Total P&L:" in line:
                        pnl_str = line.split(":")[-1].strip().replace("%", "").replace("$", "").replace("+", "")
                        result["pnl"] = float(pnl_str)
                    elif "Profit Factor:" in line:
                        result["profit_factor"] = float(line.split(":")[-1].strip())
                    elif "Max Drawdown:" in line:
                        result["max_drawdown"] = float(line.split(":")[-1].strip().replace("%", ""))
                    elif "Timeframe:" in line:
                        result["timeframe"] = line.split(":")[-1].strip()
                    elif "Range:" in line:
                        result["period"] = line.split(":")[-1].strip()

                if result.get("trades"):
                    results.append(result)
            except:
                pass

    return results


def generate_system_prompt():
    """Generate system prompt for MiniMax."""
    return """You are a BTC/USDT trading strategy optimizer. Your goal is to improve a confluence-based trading strategy by analyzing backtest results and suggesting better parameters.

The strategy uses 6 confirmations:
1. RSI - Oversold (<X) for LONG, Overbought (>X) for SHORT
2. MACD - Bullish/Bearish crossover
3. Support/Resistance - Near level with multiple touches
4. Fibonacci - Price at retracement level
5. Volume Profile (HVN) - Price at High Volume Node
6. Trend (EMA) - EMA50/EMA200 filter blocks counter-trend trades

You must respond with ONLY a JSON block containing parameter suggestions, followed by your reasoning. Format:

{
  "RSI_OVERSOLD": 30,
  "RSI_OVERBOUGHT": 70,
  "RSI_PERIOD": 14,
  "RSI_LOOKBACK": 10,
  "MACD_FAST": 12,
  "MACD_SLOW": 26,
  "MACD_SIGNAL": 9,
  "MACD_CROSS_LOOKBACK": 15,
  "EMA_FAST": 50,
  "EMA_SLOW": 200,
  "COOLDOWN_CANDLES": 6,
  "MIN_CONFIRMATIONS": 3,
  "MIN_SR_TOUCHES": 3,
  "SR_TOLERANCE_PCT": 0.5,
  "FIB_TOLERANCE_PCT": 0.5,
  "HVN_MULTIPLIER": 2.0,
  "HVN_TOLERANCE_PCT": 0.5,
  "VP_LOOKBACK": 120,
  "PAPER_DEFAULT_SL_PCT": 2.0,
  "PAPER_DEFAULT_TP_PCT": 4.0
}

Valid ranges:
- RSI_OVERSOLD: 20-45
- RSI_OVERBOUGHT: 55-80
- RSI_PERIOD: 7-21
- RSI_LOOKBACK: 3-20
- MACD_FAST: 6-20
- MACD_SLOW: 18-40
- MACD_SIGNAL: 5-15
- MACD_CROSS_LOOKBACK: 5-30
- EMA_FAST: 20-100
- EMA_SLOW: 100-300
- COOLDOWN_CANDLES: 0-12
- MIN_CONFIRMATIONS: 2-5
- MIN_SR_TOUCHES: 2-5
- SR_TOLERANCE_PCT: 0.2-1.5
- FIB_TOLERANCE_PCT: 0.2-1.5
- HVN_MULTIPLIER: 1.2-3.0
- HVN_TOLERANCE_PCT: 0.2-1.5
- VP_LOOKBACK: 60-200
- PAPER_DEFAULT_SL_PCT: 0.5-5.0
- PAPER_DEFAULT_TP_PCT: 1.0-8.0

Goal: Maximize profit factor and total P&L while keeping max drawdown < 5%."""


def generate_user_prompt(baseline_results, session_history, current_config):
    """Generate user prompt with analysis request.

    session_history: list of dicts, each with keys: suggestions, stats
    """
    prompt = f"""Baseline config performance (the target to beat):
- Win Rate: {baseline_results.get('win_rate', 0)}%
- Total P&L: {baseline_results.get('pnl', 0)}%
- Profit Factor: {baseline_results.get('profit_factor', 0)}
- Max Drawdown: {baseline_results.get('max_drawdown', 0)}%
- Trades: {baseline_results.get('trades', 0)}

Current baseline config values:
"""

    for key, value in current_config.items():
        prompt += f"  {key} = {value}\n"

    if session_history:
        prompt += f"\nThis session's previous attempts (learn from what did NOT work):\n"
        for i, entry in enumerate(session_history):
            s = entry["stats"]
            params = entry["suggestions"]
            changed = {k: v for k, v in params.items() if current_config.get(k) != v}
            changed_str = ", ".join(f"{k}={v}" for k, v in changed.items()) or "no changes"
            outcome = "BETTER" if s["pnl"] > baseline_results.get("pnl", 0) else "WORSE"
            prompt += (
                f"  Attempt {i+1}: Changed [{changed_str}] → "
                f"{s['win_rate']:.1f}% win, {s['pnl']:+.2f}% P&L, PF {s['profit_factor']:.2f}, "
                f"DD {s['max_drawdown']:.2f}% [{outcome} than baseline]\n"
            )
        prompt += "\nDo NOT repeat parameter combinations that made things worse.\n"

    prompt += """
Suggest a new set of parameters that beats the baseline. Focus on:
1. RSI thresholds - are they too wide or too tight for 1h BTC?
2. MACD lookback - too sensitive or not enough?
3. Cooldown period - too short causing over-trading after losses?
4. Confirmation requirements - need more or less confluence?
5. SL/TP ratios - optimize risk/reward ratio?

Respond with ONLY the JSON block of suggested parameters followed by a brief explanation."""

    return prompt


def run_backtest_for_config(config_file, days, timeframe):
    """Run backtest for a specific config file."""
    import importlib.util

    # Load backtester module
    spec = importlib.util.spec_from_file_location("backtester", "backtester.py")
    backtester = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(backtester)

    # Run backtest
    result = backtester.run_backtest(
        days=days,
        timeframe=timeframe,
        config_path=config_file,
        verbose=False,
        config_name=config_file
    )

    return result.to_dict()


def find_next_test_number():
    """Find next test config number."""
    max_num = 0
    for f in os.listdir("."):
        if f.startswith("btc-scanner_test") and f.endswith(".conf"):
            try:
                num = int(f.replace("btc-scanner_test", "").replace(".conf", ""))
                max_num = max(max_num, num)
            except:
                pass
    return max_num + 1


def log_result(message, to_console=True):
    """Log to both console and optimizer log file."""
    if to_console:
        print(message)

    with open(OPTIMIZER_LOG_FILE, "a") as f:
        f.write(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] {message}\n")


def run_optimizer(iterations=3, days=120, timeframe="1h", api_key=None):
    """Main optimizer loop."""
    print(f"\n{'='*60}")
    print(f"MINIMAX STRATEGY OPTIMIZER")
    print(f"{'='*60}")
    print(f"Iterations: {iterations} | Days: {days} | Timeframe: {timeframe}")

    # Load baseline config
    baseline_config = load_config("btc-scanner.conf")
    if not api_key:
        api_key = baseline_config.get("MINIMAX_API_KEY", "")

    # Run baseline backtest first
    print("\n📊 Running baseline backtest...")
    baseline_result = run_backtest_for_config("btc-scanner.conf", days, timeframe)
    baseline_stats = {
        "win_rate": baseline_result.get("win_rate", 0),
        "pnl": baseline_result.get("total_pnl_pct", 0),
        "profit_factor": baseline_result.get("profit_factor", 0),
        "max_drawdown": baseline_result.get("max_drawdown_pct", 0),
        "trades": baseline_result.get("trades_taken", 0)
    }

    print(f"Baseline: {baseline_stats['win_rate']:.1f}% win, {baseline_stats['pnl']:+.2f}% P&L, "
          f"PF {baseline_stats['profit_factor']:.2f}, DD {baseline_stats['max_drawdown']:.2f}%")

    log_result(f"Baseline: {baseline_stats['win_rate']:.1f}% win, {baseline_stats['pnl']:+.2f}% P&L, "
               f"PF {baseline_stats['profit_factor']:.2f}, DD {baseline_stats['max_drawdown']:.2f}%")

    best_config = "btc-scanner.conf"
    best_stats = baseline_stats.copy()

    system_prompt = generate_system_prompt()
    session_history = []  # Accumulates this session's attempts for MiniMax to learn from

    for i in range(1, iterations + 1):
        print(f"\n--- Iteration {i} ---")

        # Call MiniMax with full session history so it learns from previous attempts
        user_prompt = generate_user_prompt(baseline_stats, session_history, baseline_config)
        response = call_minimax(api_key, system_prompt, user_prompt)

        suggestions, reasoning = parse_minimax_response(response)

        if not suggestions:
            print("⚠️  Could not get valid suggestions, using mock parameters")
            # Generate some random but valid variations
            import random
            suggestions = {
                "RSI_OVERSOLD": random.randint(28, 38),
                "RSI_OVERBOUGHT": random.randint(62, 72),
                "MACD_CROSS_LOOKBACK": random.randint(10, 25),
                "COOLDOWN_CANDLES": random.randint(4, 10),
                "PAPER_DEFAULT_SL_PCT": round(random.uniform(1.0, 3.0), 1),
                "PAPER_DEFAULT_TP_PCT": round(random.uniform(3.0, 6.0), 1),
            }
            reasoning = "Random variation for testing"

        print(f"MiniMax suggests: {', '.join([f'{k}={v}' for k,v in suggestions.items()])}")
        print(f"Reason: {reasoning[:100]}...")

        # Create test config
        test_num = find_next_test_number()
        test_config_path = f"btc-scanner_test{test_num}.conf"

        # Merge suggestions with baseline
        test_config = baseline_config.copy()
        test_config.update(suggestions)

        # Save test config
        save_config(test_config, test_config_path)
        print(f"Config: {test_config_path}")

        # Run backtest
        print("Running backtest...")
        result = run_backtest_for_config(test_config_path, days, timeframe)

        test_stats = {
            "win_rate": result.get("win_rate", 0),
            "pnl": result.get("total_pnl_pct", 0),
            "profit_factor": result.get("profit_factor", 0),
            "max_drawdown": result.get("max_drawdown_pct", 0),
            "trades": result.get("trades_taken", 0)
        }

        print(f"Backtest: {test_stats['win_rate']:.1f}% win, {test_stats['pnl']:+.2f}% P&L, "
              f"PF {test_stats['profit_factor']:.2f}, DD {test_stats['max_drawdown']:.2f}%")

        # Record this attempt so MiniMax can learn in next iteration
        session_history.append({"suggestions": suggestions, "stats": test_stats})

        # Always compare against baseline (not previous iteration)
        improvement = test_stats["pnl"] > baseline_stats["pnl"] * 1.05  # 5% better than baseline

        if improvement:
            print(">>> IMPROVEMENT over baseline")
            log_result(f"Iteration {i}: IMPROVEMENT - {test_stats['win_rate']:.1f}% win, "
                       f"{test_stats['pnl']:+.2f}% P&L, PF {test_stats['profit_factor']:.2f}")

            if test_stats["pnl"] > best_stats["pnl"]:
                best_config = test_config_path
                best_stats = test_stats.copy()
        else:
            print(">>> No significant improvement")
            log_result(f"Iteration {i}: No improvement - {test_stats['win_rate']:.1f}% win, "
                       f"{test_stats['pnl']:+.2f}% P&L")

        # Append to backtest log
        with open(BACKTEST_LOG_FILE, "a") as f:
            f.write(f"\n{'='*60}\n")
            f.write(f"TEST CONFIG: {test_config_path}\n")
            f.write(f"{'='*60}\n")
            f.write(f"Timeframe: {timeframe} | Days: {days}\n")
            f.write(f"Suggestions: {suggestions}\n")
            f.write(f"Reasoning: {reasoning}\n\n")
            f.write(f"Trades: {test_stats['trades']} | Win Rate: {test_stats['win_rate']:.1f}%\n")
            f.write(f"P&L: {test_stats['pnl']:+.2f}% | PF: {test_stats['profit_factor']:.2f}\n")
            f.write(f"Max Drawdown: {test_stats['max_drawdown']:.2f}%\n")

        # Clean up test configs that didn't improve — keep directory tidy
        if not improvement:
            os.remove(test_config_path)
            print(f"Deleted {test_config_path} (no improvement)")

    # Print final results
    print(f"\n{'='*60}")
    print(f"BEST CONFIG: {best_config}")
    print(f"Win Rate: {best_stats['win_rate']:.1f}% | P&L: {best_stats['pnl']:+.2f}% | "
          f"Profit Factor: {best_stats['profit_factor']:.2f} | DD: {best_stats['max_drawdown']:.2f}%")
    print(f"{'='*60}\n")

    log_result(f"BEST: {best_config} - {best_stats['win_rate']:.1f}% win, "
               f"{best_stats['pnl']:+.2f}% P&L, PF {best_stats['profit_factor']:.2f}")


def main():
    parser = argparse.ArgumentParser(description="MiniMax Strategy Optimizer")
    parser.add_argument("--iterations", type=int, default=3, help="Number of optimization iterations")
    parser.add_argument("--days", type=int, default=120, help="Backtest days")
    parser.add_argument("--timeframe", type=str, default="1h", help="Timeframe (1m, 5m, 1h, 4h)")
    parser.add_argument("--api-key", type=str, help="MiniMax API key (or set in config)")

    args = parser.parse_args()

    # Load config for API key
    config = load_config("btc-scanner.conf")
    api_key = args.api_key or config.get("MINIMAX_API_KEY", "")

    run_optimizer(
        iterations=args.iterations,
        days=args.days,
        timeframe=args.timeframe,
        api_key=api_key
    )


if __name__ == "__main__":
    main()
