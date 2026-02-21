 Usage

  # Run scanner normally
  python3 btc-scanner.py

  # Run scanner in loop
  python3 btc-scanner.py --loop

  # Run backtest via backtester.py
  python3 backtester.py --days 30 --timeframe 5m --verbose

  # Run backtest via scanner
  python3 btc-scanner.py --backtest --days 30 --timeframe 5m --verbose


  # Run backtest (Best setting 1h)
  python3 backtester.py --days 30 --timeframe 1h --verbose

  # Run optimizer
  python3 optimizer.py --iterations 3 --days 120 --timeframe 1h

