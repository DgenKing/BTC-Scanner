 Usage

  # Run scanner normally
  python3 btc-scanner.py

  # Run scanner in loop
  python3 btc-scanner.py --loop

  # Run backtest via backtester.py
  python3 backtester.py --days 30 --timeframe 5m --verbose

  # Run backtest via scanner
  python3 btc-scanner.py --backtest --days 30 --timeframe 5m --verbose
