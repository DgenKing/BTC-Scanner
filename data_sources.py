#!/usr/bin/env python3
"""
Data sources for enhanced market analysis (all FREE, no API keys):
- Fear & Greed Index: alternative.me
- Reddit r/Bitcoin: public JSON
- Google Trends: pytrends
- CoinGecko: price/volume data
"""

import os
import time
import requests
import xml.etree.ElementTree as ET
from datetime import datetime

SENTIMENT_LOG_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "sentiment_log.txt")

_cache = {}  # {key: (result, timestamp)}


def _cached(key, ttl_seconds, fn):
    """Return (result, is_fresh). is_fresh=True if data was just fetched."""
    now = time.time()
    if key in _cache:
        result, ts = _cache[key]
        if now - ts < ttl_seconds:
            return result, False
    result = fn()
    _cache[key] = (result, now)
    return result, True


# ============================================================================
# COINGECKO API - Price / Volume Data
# ============================================================================

def get_crypto_cv_volume():
    """Get BTC price/volume from CoinGecko. No auth required."""
    try:
        url = "https://api.coingecko.com/api/v3/simple/price"
        params = {
            "ids": "bitcoin",
            "vs_currencies": "usd",
            "include_market_cap": "true",
            "include_24hr_vol": "true",
            "include_24hr_change": "true",
            "include_7d_change": "true"
        }
        resp = requests.get(url, params=params, timeout=10)
        resp.raise_for_status()
        data = resp.json().get("bitcoin", {})
        return {
            "symbol": "BTC",
            "price": float(data.get("usd", 0)),
            "market_cap": float(data.get("usd_market_cap", 0)),
            "volume_24h": float(data.get("usd_24h_vol", 0)),
            "change_24h": float(data.get("usd_24h_change", 0)),
            "change_7d": float(data.get("usd_7d_change", 0)),
            "timestamp": datetime.now().isoformat(),
            "source": "CoinGecko"
        }
    except Exception as e:
        print(f"⚠️  CoinGecko error: {e}")
        return None


def analyze_volume_strength(cv_volume, binance_volume):
    """Compare CoinGecko 24h volume against current Binance hourly volume."""
    if not cv_volume:
        return {"status": "no_data"}
    cv_24h_vol = cv_volume.get("volume_24h", 0)
    cv_hourly = cv_24h_vol / 24
    volume_ratio = binance_volume / cv_hourly if cv_hourly > 0 else 1
    if volume_ratio > 1.5:
        strength = "VERY_STRONG"
    elif volume_ratio > 1.2:
        strength = "STRONG"
    elif volume_ratio > 0.8:
        strength = "NORMAL"
    else:
        strength = "WEAK"
    return {
        "24h_volume_usd": cv_24h_vol,
        "current_hourly_vol": binance_volume,
        "estimated_hourly_avg": cv_hourly,
        "volume_ratio": round(volume_ratio, 2),
        "strength": strength,
        "change_24h": cv_volume.get("change_24h", 0),
        "change_7d": cv_volume.get("change_7d", 0)
    }


# ============================================================================
# FEAR & GREED INDEX - alternative.me (most reliable sentiment source)
# ============================================================================

def get_fear_greed():
    """
    Fetch the Crypto Fear & Greed Index from alternative.me.
    Returns 0-100 score: 0=Extreme Fear, 100=Extreme Greed.
    No auth, no rate limits, updates daily.
    """
    try:
        resp = requests.get("https://api.alternative.me/fng/", timeout=10)
        resp.raise_for_status()
        data = resp.json()["data"][0]
        score = int(data["value"])
        label = data["value_classification"]
        if score > 55:
            sentiment = "BULLISH"
        elif score < 45:
            sentiment = "BEARISH"
        else:
            sentiment = "NEUTRAL"
        return {
            "sentiment": sentiment,
            "score": score,
            "label": label,
            "source": "Fear&Greed Index"
        }
    except Exception as e:
        print(f"⚠️  Fear & Greed error: {e}")
        return None


# ============================================================================
# REDDIT r/Bitcoin - Public JSON (no auth needed)
# ============================================================================

BULLISH_WORDS = [
    "bullish", "moon", "hodl", "buy", "accumulate", "long", "breakout",
    "ath", "rally", "recovery", "bounce", "pump", "surge", "golden cross",
    "support", "bull run", "all time high", "uptrend"
]

BEARISH_WORDS = [
    "bearish", "crash", "dump", "sell", "correction", "rekt", "liquidation",
    "death cross", "resistance", "breakdown", "short", "bear", "collapse",
    "panic", "fear", "top is in", "bubble"
]


def get_reddit_sentiment():
    """
    Scrape r/Bitcoin hot posts via public JSON endpoint. No auth required.
    Scores post titles for bullish/bearish keywords, weighted by upvotes.
    """
    try:
        headers = {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"}
        resp = requests.get("https://www.reddit.com/r/Bitcoin/.rss", headers=headers, timeout=10)
        resp.raise_for_status()

        root = ET.fromstring(resp.text)
        ns = {"atom": "http://www.w3.org/2005/Atom"}
        entries = root.findall("atom:entry", ns)

        bullish_score = 0
        bearish_score = 0
        total_weight = len(entries) or 1

        for entry in entries:
            title_el = entry.find("atom:title", ns)
            title = title_el.text.lower() if title_el is not None else ""

            for word in BULLISH_WORDS:
                if word in title:
                    bullish_score += 1
                    break
            for word in BEARISH_WORDS:
                if word in title:
                    bearish_score += 1
                    break

        if total_weight == 0:
            return None

        net = bullish_score - bearish_score
        score = 50 + (net / total_weight) * 100
        score = round(min(100, max(0, score)), 1)

        if score > 55:
            sentiment = "BULLISH"
        elif score < 45:
            sentiment = "BEARISH"
        else:
            sentiment = "NEUTRAL"

        return {
            "sentiment": sentiment,
            "score": score,
            "posts": len(entries),
            "source": "Reddit r/Bitcoin"
        }
    except Exception as e:
        print(f"⚠️  Reddit error: {e}")
        return None


# ============================================================================
# GOOGLE TRENDS - pytrends (rising search = interest spike)
# ============================================================================

def get_google_trends_sentiment():
    """
    Use pytrends to check Bitcoin search interest over the last 7 days.
    Rising trend vs average = bullish signal (more attention/interest).
    """
    try:
        from pytrends.request import TrendReq
        pt = TrendReq(hl="en-US", tz=0, timeout=(5, 15))
        pt.build_payload(["Bitcoin"], timeframe="now 7-d")
        df = pt.interest_over_time()

        if df is None or df.empty:
            return None

        values = df["Bitcoin"].tolist()
        if len(values) < 4:
            return None

        overall_avg = sum(values) / len(values)
        recent_avg = sum(values[-4:]) / 4  # last ~4 data points

        if overall_avg == 0:
            return None

        ratio = recent_avg / overall_avg
        score = round(min(100, max(0, 50 * ratio)), 1)

        if ratio > 1.15:
            sentiment = "BULLISH"
        elif ratio < 0.85:
            sentiment = "BEARISH"
        else:
            sentiment = "NEUTRAL"

        return {
            "sentiment": sentiment,
            "score": score,
            "trend_ratio": round(ratio, 2),
            "source": "Google Trends"
        }
    except Exception as e:
        print(f"⚠️  Google Trends error: {e}")
        return None


# ============================================================================
# COMBINED SENTIMENT
# ============================================================================

def get_twitter_sentiment_sync():
    """Stub — Twitter scraping removed."""
    return None


def read_sentiment_history(n=16):
    """
    Read the last N sentiment entries from sentiment_log.txt.
    Returns a formatted string for inclusion in AI prompts.
    Each entry = one 15-min sentiment reading (n=16 → 4 hours).
    """
    try:
        if not os.path.exists(SENTIMENT_LOG_FILE):
            return "No sentiment history available yet."

        with open(SENTIMENT_LOG_FILE, "r", encoding="utf-8") as f:
            content = f.read()

        # Split into blocks by timestamp lines
        blocks = []
        current = []
        for line in content.splitlines():
            if line.startswith("[") and "]" in line and "/100)" in line:
                if current:
                    blocks.append("\n".join(current))
                current = [line]
            elif current:
                current.append(line)
        if current:
            blocks.append("\n".join(current))

        last_n = blocks[-n:] if len(blocks) >= n else blocks
        if not last_n:
            return "No sentiment history available yet."

        return "\n".join(last_n)

    except Exception as e:
        return f"Could not read sentiment history: {e}"


def get_enhanced_sentiment():
    """
    Weighted sentiment from all available sources:
      Fear & Greed Index : weight 3.0
      Reddit r/Bitcoin   : weight 2.0
      CoinGecko (price)  : weight 1.5
      Google Trends      : weight 1.0
    Any failed source is skipped gracefully.
    """
    sources = []
    any_fresh = False

    fg, fresh = _cached("fear_greed", 3600, get_fear_greed)
    any_fresh = any_fresh or fresh
    if fg:
        sources.append(("Fear&Greed", fg["score"], 3.0, fg["sentiment"]))

    rd, fresh = _cached("reddit", 900, get_reddit_sentiment)
    any_fresh = any_fresh or fresh
    if rd:
        sources.append(("Reddit", rd["score"], 2.0, rd["sentiment"]))

    cv, fresh = _cached("coingecko", 300, get_crypto_cv_volume)
    any_fresh = any_fresh or fresh
    if cv:
        change_24h = cv.get("change_24h", 0)
        change_7d = cv.get("change_7d", 0)
        avg_change = (change_24h + change_7d) / 2
        price_score = round(min(100, max(0, 50 + avg_change * 5)), 1)
        if price_score > 55:
            price_sent = "BULLISH"
        elif price_score < 45:
            price_sent = "BEARISH"
        else:
            price_sent = "NEUTRAL"
        sources.append(("CoinGecko", price_score, 1.5, price_sent))

    gt, fresh = _cached("gtrends", 3600, get_google_trends_sentiment)
    any_fresh = any_fresh or fresh
    if gt:
        sources.append(("GoogleTrends", gt["score"], 1.0, gt["sentiment"]))

    if not sources:
        return {
            "sentiment": "UNKNOWN",
            "score": 50,
            "source": "none",
            "timestamp": datetime.now().isoformat()
        }

    weighted_sum = sum(score * weight for _, score, weight, _ in sources)
    total_weight = sum(weight for _, _, weight, _ in sources)
    final_score = round(weighted_sum / total_weight, 1)

    if final_score > 55:
        sentiment = "BULLISH"
    elif final_score < 45:
        sentiment = "BEARISH"
    else:
        sentiment = "NEUTRAL"

    source_names = " + ".join(name for name, _, _, _ in sources)
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    # Save to sentiment_log.txt only when fresh data was fetched
    if any_fresh:
        try:
            with open(SENTIMENT_LOG_FILE, "a", encoding="utf-8") as f:
                f.write(f"\n[{timestamp}] {sentiment} ({final_score}/100)\n")
                for name, score, weight, sent in sources:
                    f.write(f"  {name:<14} {sent:<8} {score:>5.1f}/100  (weight {weight})\n")
        except Exception as e:
            print(f"⚠️  Could not write sentiment_log.txt: {e}")

    return {
        "sentiment": sentiment,
        "score": final_score,
        "source": source_names,
        "timestamp": datetime.now().isoformat()
    }


if __name__ == "__main__":
    print("=" * 70)
    print("DATA SOURCES TEST")
    print("=" * 70)

    print("\n🔹 Fear & Greed Index...")
    fg = get_fear_greed()
    if fg:
        print(f"  {fg['label']} ({fg['score']}/100) → {fg['sentiment']}")
    else:
        print("  ❌ Failed")

    print("\n🔹 Reddit r/Bitcoin...")
    rd = get_reddit_sentiment()
    if rd:
        print(f"  {rd['posts']} posts → {rd['sentiment']} ({rd['score']:.1f}/100)")
    else:
        print("  ❌ Failed")

    print("\n🔹 CoinGecko...")
    cv = get_crypto_cv_volume()
    if cv:
        print(f"  BTC ${cv['price']:,.2f} | 24h: {cv['change_24h']:.2f}% | Vol: ${cv['volume_24h']:,.0f}")
    else:
        print("  ❌ Failed")

    print("\n🔹 Google Trends...")
    gt = get_google_trends_sentiment()
    if gt:
        print(f"  Ratio: {gt['trend_ratio']} → {gt['sentiment']} ({gt['score']:.1f}/100)")
    else:
        print("  ❌ Failed")

    print("\n🔹 Combined Sentiment...")
    combined = get_enhanced_sentiment()
    print(f"  {combined['sentiment']} ({combined['score']:.1f}/100)")
    print(f"  Sources: {combined['source']}")
