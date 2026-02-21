#!/usr/bin/env python3
"""
BTC Real-Time Signal Bot
Scans market every 1 minute.
Combines RSI + MACD + S/R + Fib + Volume Profile + Sentiment.
Enhanced with cryptocurrency.cv API + Twitter/X (twscrape) sentiment.
Detailed logging on every scan.
"""

import os
import sys
import json
import time
import requests
from datetime import datetime

# Import enhanced data sources
try:
    from data_sources import (
        get_crypto_cv_volume,
        analyze_volume_strength,
        get_twitter_sentiment_sync,
        get_enhanced_sentiment,
        read_sentiment_history
    )
    ENHANCED_SOURCES_AVAILABLE = True
except ImportError:
    ENHANCED_SOURCES_AVAILABLE = False
    print("⚠️  data_sources module not found. Using basic sentiment only.")

# Import paper trading
try:
    from paper_trader import PaperTrader, create_paper_trader_from_config
    PAPER_TRADER_AVAILABLE = True
except ImportError:
    PAPER_TRADER_AVAILABLE = False
    print("⚠️  paper_trader module not found. Paper trading disabled.")

# Configuration
BTC_SYMBOL = "BTCUSDT"
BINANCE_URL = "https://api.binance.com/api/v3"
STATE_FILE = "/tmp/btc-signal-state.json"
CONFIG_FILE = os.path.join(os.path.dirname(__file__), "btc-scanner.conf")

# Telegram (for signals)
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")

# Log file
LOG_FILE = "/tmp/btc-scanner.log"

# Paper trader instance (initialized in run_scan if enabled)
_paper_trader = None


def load_config():
    """Load configuration from btc-scanner.conf (Python-style config)"""
    config = {}
    if os.path.exists(CONFIG_FILE):
        with open(CONFIG_FILE, "r") as f:
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
                    elif "," in value:  # List
                        value = [x.strip().strip("'\"") for x in value.strip("[]").split(",")]
                    else:
                        try:
                            value = float(value)
                        except:
                            pass
                    
                    config[key] = value
    
    return config


def get_btc_price():
    """Get current BTC price."""
    url = f"{BINANCE_URL}/ticker/price"
    params = {"symbol": BTC_SYMBOL}
    resp = requests.get(url, params=params, timeout=10)
    return float(resp.json()["price"])


def get_klines(symbol="BTCUSDT", interval="4h", limit=300):
    """Get candlestick data from Binance."""
    url = f"{BINANCE_URL}/klines"
    params = {"symbol": symbol, "interval": interval, "limit": limit}
    resp = requests.get(url, params=params, timeout=30)
    data = resp.json()
    
    candles = []
    for k in data:
        candles.append({
            "time": k[0],
            "open": float(k[1]),
            "high": float(k[2]),
            "low": float(k[3]),
            "close": float(k[4]),
            "volume": float(k[5])
        })
    return candles


def calculate_rsi(candles, period=14):
    """Calculate RSI indicator."""
    closes = [c["close"] for c in candles]
    
    deltas = [closes[i] - closes[i-1] for i in range(1, len(closes))]
    gains = [d if d > 0 else 0 for d in deltas]
    losses = [-d if d < 0 else 0 for d in deltas]
    
    avg_gain = sum(gains[-period:]) / period
    avg_loss = sum(losses[-period:]) / period
    
    if avg_loss == 0:
        return 100
    
    rs = avg_gain / avg_loss
    return 100 - (100 / (1 + rs))


def was_rsi_oversold_recently(candles, period, threshold, lookback):
    """Return True if RSI dipped to/below threshold in the last `lookback` candles."""
    for i in range(lookback):
        subset = candles[:len(candles) - i]
        if len(subset) < period + 1:
            continue
        if calculate_rsi(subset, period) <= threshold:
            return True
    return False


def was_rsi_overbought_recently(candles, period, threshold, lookback):
    """Return True if RSI rose to/above threshold in the last `lookback` candles."""
    for i in range(lookback):
        subset = candles[:len(candles) - i]
        if len(subset) < period + 1:
            continue
        if calculate_rsi(subset, period) >= threshold:
            return True
    return False


def calculate_macd(candles, fast=12, slow=26, signal=9, cross_lookback=3):
    """Proper MACD with full series and crossover detection within lookback candles."""
    closes = [c["close"] for c in candles]

    def ema(data, period):
        if len(data) < period:
            return sum(data) / len(data) if data else 0
        multiplier = 2 / (period + 1)
        ema_val = sum(data[:period]) / period
        for price in data[period:]:
            ema_val = price * multiplier + ema_val * (1 - multiplier)
        return ema_val

    # Build full MACD line series
    macd_series = []
    for i in range(len(closes)):
        if i < slow - 1:
            macd_series.append(0)
            continue
        fast_ema = ema(closes[:i+1], fast)
        slow_ema = ema(closes[:i+1], slow)
        macd_series.append(fast_ema - slow_ema)

    # Signal line (EMA of MACD)
    signal_series = []
    for i in range(len(macd_series)):
        if i < signal:
            signal_series.append(macd_series[i])
            continue
        signal_series.append(ema(macd_series[:i+1], signal))

    macd_line = macd_series[-1]
    signal_line = signal_series[-1]
    histogram = macd_line - signal_line

    # Crossover detection within the last cross_lookback candles
    crossover = None
    lookback = min(cross_lookback + 1, len(macd_series) - 1)
    for i in range(-lookback, 0):
        prev_macd = macd_series[i - 1]
        prev_sig = signal_series[i - 1]
        curr_macd = macd_series[i]
        curr_sig = signal_series[i]
        if prev_macd < prev_sig and curr_macd > curr_sig:
            crossover = "BULLISH"
            break
        elif prev_macd > prev_sig and curr_macd < curr_sig:
            crossover = "BEARISH"
            break

    return {
        "macd": macd_line,
        "signal": signal_line,
        "histogram": histogram,
        "crossover": crossover
    }


def find_support_resistance(candles, lookback=250, volume_multiplier=1.5):
    """Find support/resistance from price action."""
    current_price = candles[-1]["close"]
    avg_vol = sum(c["volume"] for c in candles[-lookback:]) / lookback

    # Find pivot highs and lows
    highs = []
    lows = []
    volumes = []

    for i in range(10, len(candles) - 10):
        # Pivot high
        if all(candles[i]["high"] > candles[j]["high"] for j in range(i-5, i+6) if j != i):
            if candles[i]["volume"] > avg_vol * volume_multiplier:
                highs.append({"price": candles[i]["high"], "volume": candles[i]["volume"]})

        # Pivot low
        if all(candles[i]["low"] < candles[j]["low"] for j in range(i-5, i+6) if j != i):
            if candles[i]["volume"] > avg_vol * volume_multiplier:
                lows.append({"price": candles[i]["low"], "volume": candles[i]["volume"]})
    
    # Get nearest levels
    support = max([l["price"] for l in lows if l["price"] < current_price], default=current_price * 0.95)
    resistance = min([h["price"] for h in highs if h["price"] > current_price], default=current_price * 1.05)
    
    # Count touches
    sr_tolerance = 0.005
    support_touches = sum(1 for l in lows if abs(l["price"] - support) / support < sr_tolerance)
    resistance_touches = sum(1 for h in highs if abs(h["price"] - resistance) / resistance < sr_tolerance)
    
    return {
        "support": support,
        "resistance": resistance,
        "current": current_price,
        "support_touches": support_touches,
        "resistance_touches": resistance_touches,
        "avg_volume": avg_vol
    }


def calculate_fibonacci(candles):
    """Calculate Fibonacci retracement levels."""
    high = max(c["high"] for c in candles[-50:])
    low = min(c["low"] for c in candles[-50:])
    diff = high - low
    
    return {
        "0.382": high - diff * 0.382,
        "0.5": high - diff * 0.5,
        "0.618": high - diff * 0.618,
        "0.786": high - diff * 0.786,
        "high": high,
        "low": low
    }


def check_fib_proximity(price, fib, tolerance_pct=0.5):
    """Check if price is near any Fibonacci level."""
    fib_levels = [0.382, 0.5, 0.618, 0.786]
    tolerance = tolerance_pct / 100

    for level in fib_levels:
        fib_price = fib[str(level)]
        if abs(price - fib_price) / price < tolerance:
            return True, level

    return False, None


def calculate_volume_profile(candles, lookback=120, num_bins=48, hvn_multiplier=2.0):
    """Calculate Volume Profile to find HVN (High Volume Nodes)."""
    prices = [c["close"] for c in candles[-lookback:]]
    volumes = [c["volume"] for c in candles[-lookback:]]

    min_price = min(prices)
    max_price = max(prices)
    bin_size = (max_price - min_price) / num_bins

    bins = [0] * num_bins

    for price, vol in zip(prices, volumes):
        bin_idx = min(int((price - min_price) / bin_size), num_bins - 1)
        bins[bin_idx] += vol

    avg_bin_vol = sum(bins) / num_bins
    hvn_threshold = avg_bin_vol * hvn_multiplier

    # Find HVN bins
    hvn_levels = []
    for i, vol in enumerate(bins):
        if vol > hvn_threshold:
            hvn_price = min_price + (i + 0.5) * bin_size
            hvn_levels.append(hvn_price)

    return {
        "hvn_levels": hvn_levels,
        "avg_volume": avg_bin_vol
    }


def check_hvn_proximity(price, vp, tolerance_pct=0.5):
    """Check if price is near a High Volume Node."""
    tolerance = tolerance_pct / 100

    for hvn in vp["hvn_levels"]:
        if abs(price - hvn) / price < tolerance:
            return True

    return False


def calculate_ema_series(candles, fast_period=50, slow_period=200):
    """
    Calculate EMA series for trend detection.
    Returns EMA values and trend direction.
    """
    closes = [c["close"] for c in candles]

    if len(closes) < slow_period:
        # Not enough data for full EMA200, try with available
        slow_period = min(slow_period, len(closes) // 2)
        fast_period = min(fast_period, slow_period // 2)

    def ema(data, period):
        if len(data) < period:
            return sum(data) / len(data) if data else 0
        multiplier = 2 / (period + 1)
        ema_val = sum(data[:period]) / period
        for price in data[period:]:
            ema_val = price * multiplier + ema_val * (1 - multiplier)
        return ema_val

    ema_fast = ema(closes, fast_period) if fast_period > 0 else 0
    ema_slow = ema(closes, slow_period) if slow_period > 0 else 0

    # Determine trend
    current_price = closes[-1]
    if ema_fast > ema_slow and current_price > ema_fast:
        trend = "BULLISH"
    elif ema_fast < ema_slow and current_price < ema_fast:
        trend = "BEARISH"
    else:
        trend = "NEUTRAL"

    return {
        "ema_fast": ema_fast,
        "ema_slow": ema_slow,
        "trend": trend,
        "fast_period": fast_period,
        "slow_period": slow_period
    }


def get_social_sentiment(config=None):
    """
    Get sentiment from multiple sources with weighted scoring.
    Priority: Twitter > Crypto.cv volume > DuckDuckGo fallback
    """
    if config is None:
        config = {}

    twitter_enabled = config.get("TWITTER_ENABLED", True)
    crypto_cv_enabled = config.get("CRYPTO_CV_ENABLED", True)
    duckduckgo_enabled = config.get("DUCKDUCKGO_ENABLED", True)

    twitter_weight = float(config.get("TWITTER_WEIGHT", 2.0))
    crypto_cv_weight = float(config.get("CRYPTO_CV_WEIGHT", 1.5))
    duckduckgo_weight = float(config.get("DUCKDUCKGO_WEIGHT", 0.5))

    sentiment_sources = []
    total_weight = 0
    weighted_score = 0

    # Try Twitter/X first (if enabled and available)
    if twitter_enabled and ENHANCED_SOURCES_AVAILABLE:
        try:
            twitter_sentiment = get_twitter_sentiment_sync()
            if twitter_sentiment:
                sentiment_sources.append({
                    "source": "Twitter/X",
                    "sentiment": twitter_sentiment["sentiment"],
                    "score": twitter_sentiment["score"],
                    "weight": twitter_weight,
                    "details": f"{twitter_sentiment['total_tweets']} tweets, {twitter_sentiment['total_engagement']} engagement"
                })
                weighted_score += twitter_sentiment["score"] * twitter_weight
                total_weight += twitter_weight
        except:
            pass

    # Try cryptocurrency.cv volume analysis (if enabled and available)
    if crypto_cv_enabled and ENHANCED_SOURCES_AVAILABLE:
        try:
            cv_data = get_crypto_cv_volume()
            if cv_data:
                # Use price change as sentiment proxy
                change_24h = cv_data.get("change_24h", 0)
                change_7d = cv_data.get("change_7d", 0)
                avg_change = (change_24h + change_7d) / 2

                if avg_change > 2:
                    sentiment = "BULLISH"
                elif avg_change < -2:
                    sentiment = "BEARISH"
                else:
                    sentiment = "NEUTRAL"

                score = 50 + (avg_change * 5)
                score = max(0, min(100, score))

                sentiment_sources.append({
                    "source": "Crypto.cv (volume)",
                    "sentiment": sentiment,
                    "score": score,
                    "weight": crypto_cv_weight,
                    "details": f"24h: {change_24h:+.2f}%, 7d: {change_7d:+.2f}%"
                })
                weighted_score += score * crypto_cv_weight
                total_weight += crypto_cv_weight
        except:
            pass

    # Fallback to DuckDuckGo (always try this)
    if duckduckgo_enabled:
        try:
            url = "https://api.duckduckgo.com/"
            params = {"q": "bitcoin sentiment", "format": "json", "no_html": 1}
            resp = requests.get(url, params=params, timeout=10)
            data = resp.json()

            text = (data.get("Abstract", "") + " " +
                    " ".join([r.get("Text", "") for r in data.get("RelatedTopics", [])[:5]])).lower()

            bullish = sum(1 for w in ["bullish", "moon", "breakout", "rally", "accumulate"] if w in text)
            bearish = sum(1 for w in ["bearish", "crash", "dump", "correction", "fear"] if w in text)

            if bullish > bearish:
                sentiment = "BULLISH"
                score = 60 + (bullish - bearish) * 5
            elif bearish > bullish:
                sentiment = "BEARISH"
                score = 40 - (bearish - bullish) * 5
            else:
                sentiment = "NEUTRAL"
                score = 50

            score = max(0, min(100, score))

            sentiment_sources.append({
                "source": "DuckDuckGo",
                "sentiment": sentiment,
                "score": score,
                "weight": duckduckgo_weight,
                "details": f"bullish:{bullish} bearish:{bearish}"
            })
            weighted_score += score * duckduckgo_weight
            total_weight += duckduckgo_weight
        except:
            pass

    # Calculate final sentiment
    if total_weight > 0:
        final_score = weighted_score / total_weight
    else:
        final_score = 50

    # Determine overall sentiment direction
    if final_score > 55:
        final_sentiment = "BULLISH"
    elif final_score < 45:
        final_sentiment = "BEARISH"
    else:
        final_sentiment = "NEUTRAL"

    return {
        "sentiment": final_sentiment,
        "score": round(final_score, 1),
        "sources": sentiment_sources,
        "weighted": len(sentiment_sources) > 1
    }


def compute_indicators(candles, config=None):
    """
    Pure indicator computation — no API calls.
    Used by both live scanning and backtesting.
    """
    if config is None:
        config = {}

    price = candles[-1]["close"]

    rsi_period = int(config.get("RSI_PERIOD", 14))
    rsi_oversold = float(config.get("RSI_OVERSOLD", 30))
    rsi_overbought = float(config.get("RSI_OVERBOUGHT", 70))
    rsi_lookback = int(config.get("RSI_LOOKBACK", 5))

    macd_fast = int(config.get("MACD_FAST", 12))
    macd_slow = int(config.get("MACD_SLOW", 26))
    macd_signal = int(config.get("MACD_SIGNAL", 9))
    macd_cross_lookback = int(config.get("MACD_CROSS_LOOKBACK", 3))

    sr_lookback = int(config.get("SUPPORT_LOOKBACK", 250))
    sr_tolerance = float(config.get("SR_TOLERANCE_PCT", 0.5))
    volume_multiplier = float(config.get("VOLUME_MULTIPLIER", 1.5))

    fib_tolerance = float(config.get("FIB_TOLERANCE_PCT", 0.5))

    vp_lookback = int(config.get("VP_LOOKBACK", 120))
    num_bins = int(config.get("NUM_BINS", 48))
    hvn_multiplier = float(config.get("HVN_MULTIPLIER", 2.0))
    hvn_tolerance = float(config.get("HVN_TOLERANCE_PCT", 0.5))
    require_hvn = config.get("REQUIRE_HVN", True)

    rsi = calculate_rsi(candles, rsi_period)
    macd = calculate_macd(candles, macd_fast, macd_slow, macd_signal, macd_cross_lookback)
    sr = find_support_resistance(candles, sr_lookback, volume_multiplier)
    fib = calculate_fibonacci(candles)
    vp = calculate_volume_profile(candles, vp_lookback, num_bins, hvn_multiplier)

    # Check confirmations
    near_support = abs(price - sr["support"]) / price * 100 < sr_tolerance
    near_resistance = abs(price - sr["resistance"]) / price * 100 < sr_tolerance
    at_fib, fib_level = check_fib_proximity(price, fib, fib_tolerance)
    at_hvn = check_hvn_proximity(price, vp, hvn_tolerance) if require_hvn else False

    rsi_oversold_confirm = rsi < rsi_oversold
    rsi_overbought_confirm = rsi > rsi_overbought

    rsi_recently_oversold = was_rsi_oversold_recently(candles, rsi_period, rsi_oversold, rsi_lookback)
    rsi_recently_overbought = was_rsi_overbought_recently(candles, rsi_period, rsi_overbought, rsi_lookback)

    rsi_oversold_confirm = rsi_oversold_confirm or rsi_recently_oversold
    rsi_overbought_confirm = rsi_overbought_confirm or rsi_recently_overbought

    # Calculate EMA trend
    ema_fast = int(config.get("EMA_FAST", 50))
    ema_slow = int(config.get("EMA_SLOW", 200))
    ema_data = calculate_ema_series(candles, ema_fast, ema_slow)

    return {
        "price": price,
        "rsi": rsi,
        "rsi_oversold": rsi_oversold,
        "rsi_overbought": rsi_overbought,
        "rsi_oversold_confirm": rsi_oversold_confirm,
        "rsi_overbought_confirm": rsi_overbought_confirm,
        "rsi_recently_oversold": rsi_recently_oversold,
        "rsi_recently_overbought": rsi_recently_overbought,
        "macd": macd,
        "macd_bullish_cross": macd.get("crossover") == "BULLISH",
        "macd_bearish_cross": macd.get("crossover") == "BEARISH",
        "macd_histogram": macd.get("histogram", 0),
        "support": sr["support"],
        "resistance": sr["resistance"],
        "near_support": near_support,
        "near_resistance": near_resistance,
        "support_touches": sr["support_touches"],
        "resistance_touches": sr["resistance_touches"],
        "fibonacci": fib,
        "at_fib": at_fib,
        "fib_level": fib_level,
        "volume_profile": vp,
        "at_hvn": at_hvn,
        "ema_fast": ema_data["ema_fast"],
        "ema_slow": ema_data["ema_slow"],
        "trend": ema_data["trend"],
    }


def analyze_market(config=None):
    """Full market analysis with live data from APIs."""
    if config is None:
        config = {}

    timeframe = str(config.get("MIN_TIMEFRAME", "4h"))
    candles = get_klines(BTC_SYMBOL, timeframe, 300)

    # Get pure indicator computations
    analysis = compute_indicators(candles, config)
    analysis["timeframe"] = timeframe

    # Add live sentiment data
    analysis["sentiment"] = get_social_sentiment(config)

    # Enhanced volume analysis from cryptocurrency.cv
    cv_volume = None
    volume_analysis = None
    if ENHANCED_SOURCES_AVAILABLE and config.get("CRYPTO_CV_ENABLED", True):
        cv_volume = get_crypto_cv_volume()
        if cv_volume:
            current_candle_vol = candles[-1]["volume"]
            volume_analysis = analyze_volume_strength(cv_volume, current_candle_vol)

    analysis["crypto_cv_volume"] = cv_volume
    analysis["volume_analysis"] = volume_analysis
    analysis["time"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    return analysis


def evaluate_trade_setup(analysis, config=None, cooldown_state=None):
    """
    Confluence-based signal - requires MIN_CONFIRMATIONS out of 5.
    Includes trend filter and cooldown mechanism.
    """
    if config is None:
        config = {}

    # Trend filter settings
    trend_filter_enabled = config.get("TREND_FILTER_ENABLED", True)
    min_sr_touches = int(config.get("MIN_SR_TOUCHES", 3))
    min_confirmations = int(config.get("MIN_CONFIRMATIONS", 3))

    price = analysis["price"]
    rsi = analysis["rsi"]
    trend = analysis.get("trend", "NEUTRAL")

    # TREND GATE: Block signals against trend
    trend_blocked = False
    if trend_filter_enabled and trend != "NEUTRAL":
        # Get preliminary counts to check direction
        long_requirements_temp = {
            "rsi_oversold": analysis["rsi_oversold_confirm"],
            "macd_bullish_cross": analysis["macd_bullish_cross"],
            "near_support": analysis["near_support"] and analysis["support_touches"] >= min_sr_touches,
            "at_fib": analysis["at_fib"],
            "at_hvn": analysis["at_hvn"]
        }
        short_requirements_temp = {
            "rsi_overbought": analysis["rsi_overbought_confirm"],
            "macd_bearish_cross": analysis["macd_bearish_cross"],
            "near_resistance": analysis["near_resistance"] and analysis["resistance_touches"] >= min_sr_touches,
            "at_fib": analysis["at_fib"],
            "at_hvn": analysis["at_hvn"]
        }

        long_count_temp = sum(1 for v in long_requirements_temp.values() if v)
        short_count_temp = sum(1 for v in short_requirements_temp.values() if v)

        # If trend is BEARISH, block LONG signals
        if trend == "BEARISH" and long_count_temp >= min_confirmations:
            trend_blocked = True
        # If trend is BULLISH, block SHORT signals
        elif trend == "BULLISH" and short_count_temp >= min_confirmations:
            trend_blocked = True

    # Build requirements (5 + trend = 6 total)
    long_requirements = {
        "rsi_oversold": analysis["rsi_oversold_confirm"],
        "macd_bullish_cross": analysis["macd_bullish_cross"],
        "near_support": analysis["near_support"] and analysis["support_touches"] >= min_sr_touches,
        "at_fib": analysis["at_fib"],
        "at_hvn": analysis["at_hvn"],
        "trend_bullish": trend == "BULLISH" or trend == "NEUTRAL"
    }

    short_requirements = {
        "rsi_overbought": analysis["rsi_overbought_confirm"],
        "macd_bearish_cross": analysis["macd_bearish_cross"],
        "near_resistance": analysis["near_resistance"] and analysis["resistance_touches"] >= min_sr_touches,
        "at_fib": analysis["at_fib"],
        "at_hvn": analysis["at_hvn"],
        "trend_bearish": trend == "BEARISH" or trend == "NEUTRAL"
    }

    long_count = sum(1 for v in long_requirements.values() if v)
    short_count = sum(1 for v in short_requirements.values() if v)

    # Determine action with trend gate
    if trend_blocked:
        action = "WAIT"
        confidence = "NONE"
        score = 0
    elif long_count >= min_confirmations and long_count >= short_count:
        action = "LONG"
        confidence = "HIGH" if long_count >= 5 else "MEDIUM"
        score = long_count * 2
    elif short_count >= min_confirmations:
        action = "SHORT"
        confidence = "HIGH" if short_count >= 5 else "MEDIUM"
        score = -(short_count * 2)
    else:
        action = "WAIT"
        confidence = "NONE"
        score = 0

    # Check cooldown after action is determined
    if cooldown_state and action in ("LONG", "SHORT"):
        if cooldown_state.get("cooldown_active", False):
            cooldown_remaining = cooldown_state.get("cooldown_remaining", 0)
            cooldown_direction = cooldown_state.get("cooldown_direction", "")

            # If same direction as cooldown, block the signal
            if cooldown_direction == action and cooldown_remaining > 0:
                action = "WAIT"
                confidence = "NONE"
                score = 0
                trend_blocked = True

    # Build confirmation messages for log (direction-aware)
    confirmations = []
    failed = []

    # RSI - report whichever direction is relevant
    rsi = analysis["rsi"]
    rsi_oversold = analysis["rsi_oversold"]
    rsi_overbought = analysis["rsi_overbought"]
    if analysis["rsi_oversold_confirm"]:
        tag = " (recently)" if analysis["rsi_recently_oversold"] and rsi >= rsi_oversold else ""
        confirmations.append(f"✓ RSI oversold{tag} ({rsi:.1f})")
    elif analysis["rsi_overbought_confirm"]:
        tag = " (recently)" if analysis["rsi_recently_overbought"] and rsi <= rsi_overbought else ""
        confirmations.append(f"✓ RSI overbought{tag} ({rsi:.1f})")
    else:
        failed.append(f"✗ RSI neutral ({rsi:.1f})")

    # MACD
    if analysis["macd_bullish_cross"]:
        confirmations.append("✓ MACD BULLISH CROSSOVER")
    elif analysis["macd_bearish_cross"]:
        confirmations.append("✓ MACD BEARISH CROSSOVER")
    else:
        failed.append("✗ No MACD crossover")

    # Support - only relevant for LONG
    if action in ("LONG", "WAIT"):
        if analysis["near_support"] and analysis["support_touches"] >= min_sr_touches:
            confirmations.append(f"✓ At solid SUPPORT (${analysis['support']:,.0f}, {analysis['support_touches']} touches)")
        elif analysis["near_support"]:
            failed.append(f"✗ Support touches too low ({analysis['support_touches']})")
        else:
            failed.append(f"✗ NOT at support (${analysis['support']:,.0f})")

    # Resistance - only relevant for SHORT
    if action in ("SHORT", "WAIT"):
        if analysis["near_resistance"] and analysis["resistance_touches"] >= min_sr_touches:
            confirmations.append(f"✓ At solid RESISTANCE (${analysis['resistance']:,.0f}, {analysis['resistance_touches']} touches)")
        elif analysis["near_resistance"]:
            failed.append(f"✗ Resistance touches too low ({analysis['resistance_touches']})")
        else:
            failed.append(f"✗ NOT at resistance (${analysis['resistance']:,.0f})")

    # Fib and HVN apply to both directions
    if analysis["at_fib"]:
        confirmations.append(f"✓ At Fibonacci {analysis['fib_level']}")
    else:
        failed.append("✗ NOT at Fibonacci zone")

    if analysis["at_hvn"]:
        confirmations.append("✓ At High Volume Node")
    else:
        failed.append("✗ NOT at HVN")

    # Trend confirmation
    if trend == "BULLISH":
        confirmations.append(f"✓ Trend BULLISH (EMA{config.get('EMA_FAST', 50)} > EMA{config.get('EMA_SLOW', 200)})")
    elif trend == "BEARISH":
        confirmations.append(f"✓ Trend BEARISH (EMA{config.get('EMA_SLOW', 200)} > EMA{config.get('EMA_FAST', 50)})")
    else:
        confirmations.append("✓ Trend NEUTRAL (ranging)")

    # Log if trend blocked the signal
    if trend_blocked:
        failed.append(f"✗ Signal blocked by trend filter ({trend})")

    return {
        "action": action,
        "confidence": confidence,
        "score": score,
        "confirmations": confirmations,
        "failed": failed,
        "trend": trend
    }


def load_state():
    """Load last signal state."""
    if os.path.exists(STATE_FILE):
        with open(STATE_FILE, "r") as f:
            return json.load(f)
    return {"last_action": None, "last_price": None, "last_time": None}


def save_state(state):
    """Save signal state."""
    with open(STATE_FILE, "w") as f:
        json.dump(state, f)


def send_alert(analysis, trade, ai_rec=None):
    """Send trade alert to Telegram."""
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        return

    price = analysis["price"]
    rsi = analysis["rsi"]
    sentiment = analysis["sentiment"]

    msg = f"🚨 BTC TRADE ALERT - {analysis['time']}\n\n"
    msg += f"💰 PRICE: ${price:,.0f}\n"
    msg += f"⏰ TIMEFRAME: {analysis['timeframe']}\n"
    msg += f"📊 RSI: {rsi:.1f} (oversold < {analysis['rsi_oversold']}, overbought > {analysis['rsi_overbought']})\n"
    msg += f"🗣️ SENTIMENT: {sentiment['sentiment']} ({sentiment['score']:.0f}/100)\n\n"

    msg += f"🎯 ACTION: {trade['action']}\n"
    msg += f"📈 CONFIDENCE: {trade['confidence']}\n"
    msg += f"📊 SCORE: {trade['score']}\n\n"

    msg += "📍 KEY LEVELS:\n"
    msg += f"  Support: ${analysis['support']:,.0f}\n"
    msg += f"  Resistance: ${analysis['resistance']:,.0f}\n\n"

    msg += "✅ CONFIRMATIONS:\n"
    for c in trade['confirmations']:
        msg += f"  {c}\n"

    if ai_rec:
        msg += "\n🤖 AI TRADE LEVELS:\n"
        if ai_rec.get("entry"):
            msg += f"  Entry:       {ai_rec['entry']}\n"
        if ai_rec.get("take_profit"):
            msg += f"  Take Profit: {ai_rec['take_profit']}\n"
        if ai_rec.get("stop_loss"):
            msg += f"  Stop Loss:   {ai_rec['stop_loss']}\n"
        if ai_rec.get("risk_reward"):
            msg += f"  Risk/Reward: {ai_rec['risk_reward']}\n"
        if ai_rec.get("advice"):
            msg += f"\n💡 {ai_rec['advice']}\n"
    
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    data = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": msg,
        "parse_mode": "HTML"
    }
    
    requests.post(url, json=data, timeout=30)
    print(f"Alert sent: {trade['action']}")


def write_log(analysis, trade):
    """Write detailed log to file."""
    log_lines = []
    log_lines.append(f"\n{'='*60}")
    log_lines.append(f"BTC SCANNER LOG - {analysis['time']}")
    log_lines.append(f"{'='*60}")
    
    log_lines.append(f"\nMARKET DATA:")
    log_lines.append(f"  Price:      ${analysis['price']:,.2f}")
    log_lines.append(f"  Timeframe:  {analysis['timeframe']}")
    
    log_lines.append(f"\nINDICATOR READINGS:")
    log_lines.append(f"  RSI:        {analysis['rsi']:.1f} (oversold < {analysis['rsi_oversold']}, overbought > {analysis['rsi_overbought']})")
    log_lines.append(f"  MACD:       {analysis['macd']['macd']:.2f}")
    log_lines.append(f"  Signal:     {analysis['macd']['signal']:.2f}")
    log_lines.append(f"  Histogram:  {analysis['macd']['histogram']:.2f}")
    log_lines.append(f"  Crossover:  {analysis['macd'].get('crossover', 'NONE')}")
    
    log_lines.append(f"\nSUPPORT/RESISTANCE:")
    log_lines.append(f"  Support:    ${analysis['support']:,.0f} ({analysis['support_touches']} touches)")
    log_lines.append(f"  Resistance: ${analysis['resistance']:,.0f} ({analysis['resistance_touches']} touches)")
    log_lines.append(f"  Near S/R:   {'Support' if analysis['near_support'] else 'Resistance' if analysis['near_resistance'] else 'None'}")
    
    log_lines.append(f"\nFIBONACCI:")
    log_lines.append(f"  Level 0.382: ${analysis['fibonacci']['0.382']:,.0f}")
    log_lines.append(f"  Level 0.5:   ${analysis['fibonacci']['0.5']:,.0f}")
    log_lines.append(f"  Level 0.618: ${analysis['fibonacci']['0.618']:,.0f}")
    log_lines.append(f"  Level 0.786: ${analysis['fibonacci']['0.786']:,.0f}")
    log_lines.append(f"  At Fib Zone: {'Yes - ' + str(analysis['fib_level']) if analysis['at_fib'] else 'No'}")
    
    log_lines.append(f"\nVOLUME PROFILE:")
    log_lines.append(f"  At HVN:     {'Yes' if analysis['at_hvn'] else 'No'}")
    log_lines.append(f"  HVN Levels: {len(analysis['volume_profile']['hvn_levels'])} found")

    # Trend filter info
    trend = analysis.get("trend", "NEUTRAL")
    ema_fast = analysis.get("ema_fast", 0)
    ema_slow = analysis.get("ema_slow", 0)
    log_lines.append(f"\nTREND FILTER:")
    log_lines.append(f"  EMA 50:    ${ema_fast:,.0f}")
    log_lines.append(f"  EMA 200:   ${ema_slow:,.0f}")
    log_lines.append(f"  Trend:     {trend}")

    sentiment = analysis['sentiment']
    log_lines.append(f"\nSENTIMENT: {sentiment['sentiment']} ({sentiment['score']:.0f}/100)")
    if sentiment.get('sources'):
        log_lines.append(f"  Sources:")
        for src in sentiment['sources']:
            log_lines.append(f"    • {src['source']}: {src['sentiment']} ({src['score']:.0f}/100) - {src.get('details', '')}")
    
    log_lines.append(f"\n{'='*60}")
    log_lines.append(f"TRADE DECISION:")
    log_lines.append(f"  Action:     {trade['action']}")
    log_lines.append(f"  Confidence: {trade['confidence']}")
    log_lines.append(f"  Score:      {trade['score']}")
    log_lines.append(f"{'='*60}")
    
    log_lines.append(f"\nCONFIRMATIONS (ALL REQUIRED):")
    for c in trade.get('confirmations', []):
        log_lines.append(f"  {c}")
    
    if trade.get('failed'):
        log_lines.append(f"\nFAILED REQUIREMENTS:")
        for f in trade['failed']:
            log_lines.append(f"  {f}")
    
    log_lines.append(f"\n{'='*60}\n")
    
    # Append to log file
    with open(LOG_FILE, "a") as f:
        f.write("\n".join(log_lines))


def print_detailed_log(analysis, trade):
    """Print detailed log of scan results."""
    print(f"\n{'='*60}")
    print(f"🔍 BTC SCANNER LOG - {analysis['time']}")
    print(f"{'='*60}")
    
    print(f"\n📊 MARKET DATA:")
    print(f"  Price:      ${analysis['price']:,.2f}")
    print(f"  Timeframe:  {analysis['timeframe']}")
    
    print(f"\n📈 INDICATOR READINGS:")
    print(f"  RSI:        {analysis['rsi']:.1f} (oversold < {analysis['rsi_oversold']}, overbought > {analysis['rsi_overbought']})")
    print(f"  MACD:       {analysis['macd']['macd']:.2f}")
    print(f"  Signal:     {analysis['macd']['signal']:.2f}")
    print(f"  Histogram:  {analysis['macd']['histogram']:.2f}")
    print(f"  Crossover:  {analysis['macd'].get('crossover', 'NONE')}")
    
    print(f"\n📍 SUPPORT/RESISTANCE:")
    print(f"  Support:    ${analysis['support']:,.0f} ({analysis['support_touches']} touches)")
    print(f"  Resistance: ${analysis['resistance']:,.0f} ({analysis['resistance_touches']} touches)")
    print(f"  Near S/R:   {'Support' if analysis['near_support'] else 'Resistance' if analysis['near_resistance'] else 'None'}")
    
    print(f"\n🔢 FIBONACCI:")
    print(f"  Level 0.382: ${analysis['fibonacci']['0.382']:,.0f}")
    print(f"  Level 0.5:   ${analysis['fibonacci']['0.5']:,.0f}")
    print(f"  Level 0.618: ${analysis['fibonacci']['0.618']:,.0f}")
    print(f"  Level 0.786: ${analysis['fibonacci']['0.786']:,.0f}")
    print(f"  At Fib Zone: {'Yes - ' + str(analysis['fib_level']) if analysis['at_fib'] else 'No'}")
    
    print(f"\n📊 VOLUME PROFILE:")
    print(f"  At HVN:     {'Yes' if analysis['at_hvn'] else 'No'}")
    print(f"  HVN Levels: {len(analysis['volume_profile']['hvn_levels'])} found")

    # Trend filter info
    trend = analysis.get("trend", "NEUTRAL")
    ema_fast = analysis.get("ema_fast", 0)
    ema_slow = analysis.get("ema_slow", 0)
    trend_emoji = "🟢" if trend == "BULLISH" else "🔴" if trend == "BEARISH" else "⚪"
    print(f"\n📈 TREND FILTER:")
    print(f"  EMA 50:    ${ema_fast:,.0f}")
    print(f"  EMA 200:   ${ema_slow:,.0f}")
    print(f"  Trend:     {trend_emoji} {trend}")

    sentiment = analysis['sentiment']
    print(f"\n🗣️ SENTIMENT: {sentiment['sentiment']} ({sentiment['score']:.0f}/100)")
    if sentiment.get('sources'):
        for src in sentiment['sources']:
            print(f"   └─ {src['source']}: {src['sentiment']} ({src['score']:.0f}/100) - {src.get('details', '')}")
    
    print(f"\n{'='*60}")
    print(f"🎯 TRADE DECISION:")
    print(f"  Action:     {trade['action']}")
    print(f"  Confidence: {trade['confidence']}")
    print(f"  Score:      {trade['score']}")
    print(f"{'='*60}")
    
    print(f"\n✅ CONFIRMATIONS (ALL REQUIRED):")
    for c in trade.get('confirmations', []):
        print(f"  {c}")
    
    if trade.get('failed'):
        print(f"\n❌ FAILED REQUIREMENTS:")
        for f in trade['failed']:
            print(f"  {f}")
    
    print(f"\n{'='*60}\n")


def print_ai_recommendation(ai_rec):
    """Print AI trade recommendation to console."""
    print(f"\n{'='*60}")
    print(f"🤖 AI TRADE ANALYSIS (DeepSeek)")
    print(f"{'='*60}")
    if ai_rec.get("entry"):
        print(f"  Entry:       {ai_rec['entry']}")
    if ai_rec.get("take_profit"):
        print(f"  Take Profit: {ai_rec['take_profit']}")
    if ai_rec.get("stop_loss"):
        print(f"  Stop Loss:   {ai_rec['stop_loss']}")
    if ai_rec.get("risk_reward"):
        print(f"  Risk/Reward: {ai_rec['risk_reward']}")
    if ai_rec.get("advice"):
        print(f"\n  Advice: {ai_rec['advice']}")
    print(f"{'='*60}\n")


def log_ai_recommendation(ai_rec):
    """Append AI recommendation to log file."""
    lines = [
        f"\n{'='*60}",
        f"AI TRADE ANALYSIS (DeepSeek)",
        f"{'='*60}",
    ]
    if ai_rec.get("entry"):
        lines.append(f"  Entry:       {ai_rec['entry']}")
    if ai_rec.get("take_profit"):
        lines.append(f"  Take Profit: {ai_rec['take_profit']}")
    if ai_rec.get("stop_loss"):
        lines.append(f"  Stop Loss:   {ai_rec['stop_loss']}")
    if ai_rec.get("risk_reward"):
        lines.append(f"  Risk/Reward: {ai_rec['risk_reward']}")
    if ai_rec.get("advice"):
        lines.append(f"  Advice: {ai_rec['advice']}")
    lines.append(f"{'='*60}\n")
    with open(LOG_FILE, "a") as f:
        f.write("\n".join(lines))


def get_ai_trade_analysis(analysis, trade, api_key):
    """Call DeepSeek AI to get entry, take profit, stop loss and risk advice."""
    price = analysis["price"]
    fib = analysis["fibonacci"]
    confirmations = "\n".join(trade.get("confirmations", []))
    failed = "\n".join(trade.get("failed", []))

    # Load last 4 hours of sentiment history (16 x 15-min readings)
    sentiment_history = "No sentiment history available."
    try:
        if ENHANCED_SOURCES_AVAILABLE:
            sentiment_history = read_sentiment_history(n=16)
    except Exception:
        pass

    prompt = f"""You are a professional BTC/USDT trade analyst. Based on the market data below, provide specific trade levels and risk management advice.

TRADE SIGNAL: {trade['action']} ({trade['confidence']} confidence, score: {trade['score']})
Price: ${price:,.2f} | Timeframe: {analysis['timeframe']}

INDICATORS:
RSI: {analysis['rsi']:.1f} (oversold<{analysis['rsi_oversold']}, overbought>{analysis['rsi_overbought']})
MACD line: {analysis['macd']['macd']:.2f} | Signal: {analysis['macd']['signal']:.2f} | Histogram: {analysis['macd']['histogram']:.2f} | Crossover: {analysis['macd'].get('crossover', 'None')}

KEY LEVELS:
Support: ${analysis['support']:,.0f} ({analysis['support_touches']} touches)
Resistance: ${analysis['resistance']:,.0f} ({analysis['resistance_touches']} touches)
Fib 0.382: ${fib['0.382']:,.0f} | 0.5: ${fib['0.5']:,.0f} | 0.618: ${fib['0.618']:,.0f} | 0.786: ${fib['0.786']:,.0f}
Price at Fib: {analysis['fib_level'] if analysis['at_fib'] else 'None'}
HVN nearby: {'Yes' if analysis['at_hvn'] else 'No'}

CONFIRMED CONDITIONS:
{confirmations if confirmations else 'None'}

FAILED CONDITIONS:
{failed if failed else 'None'}

SENTIMENT HISTORY (last 4 hours, 15-min intervals — Fear&Greed/Reddit/CoinGecko/GoogleTrends weighted):
{sentiment_history}

Respond in EXACTLY this format (no extra text):
ENTRY: $X
TAKE_PROFIT: $X (X% gain)
STOP_LOSS: $X (X% risk)
RISK_REWARD: X:1
ADVICE: [2-3 sentences of practical risk management advice for this specific setup, factoring in the sentiment trend]"""

    try:
        resp = requests.post(
            "https://api.deepseek.com/chat/completions",
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            json={
                "model": "deepseek-chat",
                "messages": [{"role": "user", "content": prompt}],
                "max_tokens": 300,
                "temperature": 0.3
            },
            timeout=30
        )
        resp.raise_for_status()
        raw = resp.json()["choices"][0]["message"]["content"].strip()

        result = {"entry": None, "take_profit": None, "stop_loss": None,
                  "risk_reward": None, "advice": None, "raw": raw}
        for line in raw.splitlines():
            if line.startswith("ENTRY:"):
                result["entry"] = line.split(":", 1)[1].strip()
            elif line.startswith("TAKE_PROFIT:"):
                result["take_profit"] = line.split(":", 1)[1].strip()
            elif line.startswith("STOP_LOSS:"):
                result["stop_loss"] = line.split(":", 1)[1].strip()
            elif line.startswith("RISK_REWARD:"):
                result["risk_reward"] = line.split(":", 1)[1].strip()
            elif line.startswith("ADVICE:"):
                result["advice"] = line.split(":", 1)[1].strip()
        return result

    except Exception as e:
        print(f"⚠️  AI analysis failed: {e}")
        return None


def run_scan():
    """Run one scan cycle."""
    state = load_state()
    config = load_config()
    
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"\n[{timestamp}] 🔄 Scanning BTC...")
    
    try:
        analysis = analyze_market(config)
        trade = evaluate_trade_setup(analysis, config)
        
        # Always print detailed log
        print_detailed_log(analysis, trade)
        
        # Always write to log file
        write_log(analysis, trade)
        
        action = trade["action"]

        # AI analysis - run every scan when signal is LONG or SHORT
        ai_rec = None
        if action in ("LONG", "SHORT"):
            api_key = config.get("DEEPSEEK_API_KEY", "")
            if api_key and api_key != "your-key-here":
                print("🤖 Requesting AI trade analysis...")
                ai_rec = get_ai_trade_analysis(analysis, trade, api_key)
                if ai_rec:
                    print_ai_recommendation(ai_rec)
                    log_ai_recommendation(ai_rec)

        # Paper Trading - update and manage positions
        global _paper_trader
        paper_enabled = config.get("PAPER_TRADING_ENABLED", False)

        if paper_enabled and PAPER_TRADER_AVAILABLE:
            # Initialize paper trader if not already done
            if _paper_trader is None:
                _paper_trader = create_paper_trader_from_config(config)
                _paper_trader.load_state()
                print(f"📋 Paper Trading initialized: ${_paper_trader.balance:.2f} balance")

            # Update existing positions with current price
            if _paper_trader.positions:
                closed_positions = _paper_trader.update_positions(
                    analysis["price"],
                    analysis["time"]
                )
                for pos in closed_positions:
                    pnl_emoji = "✅" if pos["pnl_usd"] > 0 else "❌"
                    print(f"{pnl_emoji} Paper trade closed: {pos['direction']} | "
                          f"P&L: ${pos['pnl_usd']:+,.2f} ({pos['pnl_pct']:+.2f}%) | "
                          f"Reason: {pos['exit_reason']}")

            # Check for signal flip - close opposite positions
            if action == "LONG" and any(p["direction"] == "SHORT" for p in _paper_trader.positions):
                print("⚠️  Signal flipped to LONG - closing SHORT position")
                _paper_trader.close_all_positions(analysis["price"], analysis["time"], "signal_flip")
            elif action == "SHORT" and any(p["direction"] == "LONG" for p in _paper_trader.positions):
                print("⚠️  Signal flipped to SHORT - closing LONG position")
                _paper_trader.close_all_positions(analysis["price"], analysis["time"], "signal_flip")

            # Open new position on signal
            if action in ("LONG", "SHORT") and _paper_trader.can_open():
                confirmations = len(trade.get("confirmations", []))
                pos = _paper_trader.open_position(
                    action,
                    analysis["price"],
                    analysis["time"],
                    ai_rec,
                    confirmations=confirmations,
                    confidence=trade["confidence"]
                )
                if pos:
                    print(f"📋 Paper position opened: {action} at ${analysis['price']:,.0f} | "
                          f"Size: ${pos['size_usd']:,.0f} | TP: ${pos['take_profit']:,.0f} | "
                          f"SL: ${pos['stop_loss']:,.0f}")

            # Save state
            _paper_trader.save_state()

            # Print paper trading status
            _paper_trader.print_status()

        # Check if we should alert (Telegram)
        alert = False

        if state["last_action"] != action:
            alert = True
            print(f"⚠️  SIGNAL CHANGED: {state['last_action']} → {action}")
        elif action in ["LONG", "SHORT"] and trade["confidence"] == "HIGH":
            alert = True
            print(f"⚠️  HIGH CONFIDENCE SETUP: {action}")

        if alert:
            send_alert(analysis, trade, ai_rec)
            save_state({
                "last_action": action,
                "last_price": analysis["price"],
                "last_time": analysis["time"]
            })
            
    except Exception as e:
        print(f"❌ Error: {e}")
        import traceback
        traceback.print_exc()


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "--loop":
        # Run continuous loop - every 60 seconds
        print("🚀 BTC Signal Bot running (1 min interval, Ctrl+C to stop)...")
        print(f"Config: {CONFIG_FILE}")
        while True:
            run_scan()
            time.sleep(60)  # 1 minute
    elif len(sys.argv) > 1 and sys.argv[1] == "--backtest":
        # Run backtest
        import importlib.util

        # Parse args
        days = 30
        timeframe = "5m"
        verbose = False

        i = 2
        while i < len(sys.argv):
            if sys.argv[i] == "--days" and i + 1 < len(sys.argv):
                days = int(sys.argv[i + 1])
                i += 2
            elif sys.argv[i] == "--timeframe" and i + 1 < len(sys.argv):
                timeframe = sys.argv[i + 1]
                i += 2
            elif sys.argv[i] == "--verbose":
                verbose = True
                i += 1
            else:
                i += 1

        print(f"Running backtest: {days} days, {timeframe} timeframe")

        # Load and run backtester
        spec = importlib.util.spec_from_file_location("backtester", "backtester.py")
        backtester = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(backtester)

        backtester.run_backtest(days=days, timeframe=timeframe, verbose=verbose)
    else:
        # Single scan
        run_scan()
