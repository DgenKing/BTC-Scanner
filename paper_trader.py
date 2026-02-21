#!/usr/bin/env python3
"""
Paper Trading Module
Simulates trades using live signals without real money.
"""

import json
import os
from datetime import datetime
from typing import Optional, Dict, List, Any


class PaperTrader:
    """Manages virtual portfolio and open positions for paper trading."""

    def __init__(
        self,
        starting_balance: float = 10000.0,
        max_positions: int = 1,
        position_size_pct: float = 10.0,
        default_sl_pct: float = 2.0,
        default_tp_pct: float = 4.0,
        trailing_stop_pct: float = 0.0,
        state_file: str = "paper_trades.json"
    ):
        self.starting_balance = starting_balance
        self.balance = starting_balance
        self.max_positions = max_positions
        self.position_size_pct = position_size_pct
        self.default_sl_pct = default_sl_pct
        self.default_tp_pct = default_tp_pct
        self.trailing_stop_pct = trailing_stop_pct
        self.state_file = state_file

        self.positions: List[Dict] = []
        self.trade_history: List[Dict] = []
        self.next_trade_id = 1

    def can_open(self) -> bool:
        """Check if we can open a new position."""
        return len(self.positions) < self.max_positions

    def open_position(
        self,
        signal: str,
        price: float,
        timestamp: str,
        ai_rec: Optional[Dict] = None,
        confirmations: int = 0,
        confidence: str = "NONE"
    ) -> Optional[Dict]:
        """
        Open a virtual position based on signal.

        Args:
            signal: "LONG" or "SHORT"
            price: Current BTC price
            timestamp: Current timestamp
            ai_rec: Optional AI recommendation with entry/tp/sl
            confirmations: Number of confirmations
            confidence: Confidence level

        Returns:
            Position dict if opened, None if cannot open
        """
        if not self.can_open():
            return None

        # Calculate position size
        size_usd = self.balance * (self.position_size_pct / 100)
        btc_size = size_usd / price

        # Determine entry, take profit, stop loss
        if ai_rec and ai_rec.get("entry"):
            # Parse AI entry price from string like "$97,500" or "97500"
            entry_str = ai_rec["entry"].replace("$", "").replace(",", "").strip()
            try:
                entry_price = float(entry_str)
            except ValueError:
                entry_price = price  # Use current price if parsing fails
        else:
            entry_price = price

        if ai_rec and ai_rec.get("stop_loss"):
            sl_str = ai_rec["stop_loss"].replace("$", "").replace("%", "").replace(",", "").strip()
            try:
                sl_pct = float(sl_str)
                if signal == "LONG":
                    stop_loss = entry_price * (1 - sl_pct / 100)
                else:
                    stop_loss = entry_price * (1 + sl_pct / 100)
            except ValueError:
                # Use default percentage
                if signal == "LONG":
                    stop_loss = entry_price * (1 - self.default_sl_pct / 100)
                else:
                    stop_loss = entry_price * (1 + self.default_sl_pct / 100)
        else:
            # Default stop loss
            if signal == "LONG":
                stop_loss = entry_price * (1 - self.default_sl_pct / 100)
            else:
                stop_loss = entry_price * (1 + self.default_sl_pct / 100)

        if ai_rec and ai_rec.get("take_profit"):
            tp_str = ai_rec["take_profit"].replace("$", "").replace("%", "").replace(",", "").strip()
            try:
                tp_pct = float(tp_str)
                if signal == "LONG":
                    take_profit = entry_price * (1 + tp_pct / 100)
                else:
                    take_profit = entry_price * (1 - tp_pct / 100)
            except ValueError:
                # Use default percentage
                if signal == "LONG":
                    take_profit = entry_price * (1 + self.default_tp_pct / 100)
                else:
                    take_profit = entry_price * (1 - self.default_tp_pct / 100)
        else:
            # Default take profit
            if signal == "LONG":
                take_profit = entry_price * (1 + self.default_tp_pct / 100)
            else:
                take_profit = entry_price * (1 - self.default_tp_pct / 100)

        position = {
            "id": self.next_trade_id,
            "direction": signal,
            "entry_price": entry_price,
            "take_profit": take_profit,
            "stop_loss": stop_loss,
            "size_btc": btc_size,
            "size_usd": size_usd,
            "entry_time": timestamp,
            "confirmations": confirmations,
            "confidence": confidence,
            "trailing_stop_enabled": self.trailing_stop_pct > 0,
            "trailing_stop_price": None,
            "highest_price": price if signal == "LONG" else price,
            "lowest_price": price if signal == "SHORT" else price
        }

        self.positions.append(position)
        self.next_trade_id += 1

        # Deduct from balance
        self.balance -= size_usd

        return position

    def update_positions(
        self,
        current_price: float,
        timestamp: str,
        exit_reason: Optional[str] = None
    ) -> List[Dict]:
        """
        Check open positions against current price.
        Closes positions if TP or SL is hit.

        Args:
            current_price: Current BTC price
            timestamp: Current timestamp
            exit_reason: Optional manual exit reason

        Returns:
            List of closed positions
        """
        closed = []

        for position in list(self.positions):
            direction = position["direction"]
            entry = position["entry_price"]
            tp = position["take_profit"]
            sl = position["stop_loss"]

            # Update highest/lowest price for trailing stop
            if direction == "LONG":
                if current_price > position["highest_price"]:
                    position["highest_price"] = current_price
                    # Update trailing stop if enabled
                    if position["trailing_stop_enabled"] and self.trailing_stop_pct > 0:
                        new_ts = current_price * (1 - self.trailing_stop_pct / 100)
                        if position["trailing_stop_price"] is None or new_ts > position["trailing_stop_price"]:
                            position["trailing_stop_price"] = new_ts
            else:
                if current_price < position["lowest_price"]:
                    position["lowest_price"] = current_price
                    if position["trailing_stop_enabled"] and self.trailing_stop_pct > 0:
                        new_ts = current_price * (1 + self.trailing_stop_pct / 100)
                        if position["trailing_stop_price"] is None or new_ts < position["trailing_stop_price"]:
                            position["trailing_stop_price"] = new_ts

            # Check exit conditions
            should_close = False
            actual_exit_reason = None

            if direction == "LONG":
                # Check take profit
                if current_price >= tp:
                    should_close = True
                    actual_exit_reason = "take_profit"
                # Check stop loss
                elif current_price <= sl:
                    should_close = True
                    actual_exit_reason = "stop_loss"
                # Check trailing stop
                elif position["trailing_stop_enabled"] and position["trailing_stop_price"]:
                    if current_price <= position["trailing_stop_price"]:
                        should_close = True
                        actual_exit_reason = "trailing_stop"
            else:  # SHORT
                # Check take profit
                if current_price <= tp:
                    should_close = True
                    actual_exit_reason = "take_profit"
                # Check stop loss
                elif current_price >= sl:
                    should_close = True
                    actual_exit_reason = "stop_loss"
                # Check trailing stop
                elif position["trailing_stop_enabled"] and position["trailing_stop_price"]:
                    if current_price >= position["trailing_stop_price"]:
                        should_close = True
                        actual_exit_reason = "trailing_stop"

            # Manual exit reason (signal flip, etc)
            if exit_reason:
                should_close = True
                actual_exit_reason = exit_reason

            if should_close:
                # Calculate P&L
                if direction == "LONG":
                    pnl_usd = (current_price - entry) * position["size_btc"]
                    pnl_pct = (current_price - entry) / entry * 100
                else:  # SHORT
                    pnl_usd = (entry - current_price) * position["size_btc"]
                    pnl_pct = (entry - current_price) / entry * 100

                # Add to balance
                self.balance += (position["size_usd"] + pnl_usd)

                # Record in history
                closed_position = position.copy()
                closed_position["exit_price"] = current_price
                closed_position["exit_time"] = timestamp
                closed_position["exit_reason"] = actual_exit_reason
                closed_position["pnl_usd"] = round(pnl_usd, 2)
                closed_position["pnl_pct"] = round(pnl_pct, 2)

                self.trade_history.append(closed_position)
                self.positions.remove(position)
                closed.append(closed_position)

        return closed

    def close_all_positions(self, current_price: float, timestamp: str, reason: str = "manual") -> List[Dict]:
        """Close all open positions."""
        closed = []
        for _ in range(len(self.positions)):
            pos = self.update_positions(current_price, timestamp, exit_reason=reason)
            closed.extend(pos)
        return closed

    def get_stats(self) -> Dict[str, Any]:
        """Get portfolio statistics."""
        if not self.trade_history:
            return {
                "total_trades": 0,
                "winning_trades": 0,
                "losing_trades": 0,
                "win_rate": 0.0,
                "total_pnl_usd": 0.0,
                "total_pnl_pct": 0.0,
                "max_drawdown_pct": 0.0,
                "profit_factor": 0.0,
                "avg_win_usd": 0.0,
                "avg_loss_usd": 0.0,
                "best_trade_pct": 0.0,
                "worst_trade_pct": 0.0,
                "open_positions": len(self.positions),
                "current_balance": self.balance
            }

        winning = [t for t in self.trade_history if t["pnl_usd"] > 0]
        losing = [t for t in self.trade_history if t["pnl_usd"] <= 0]

        total_wins = sum(t["pnl_usd"] for t in winning)
        total_losses = abs(sum(t["pnl_usd"] for t in losing))

        # Calculate max drawdown
        peak = self.starting_balance
        max_dd = 0
        running = self.starting_balance
        for trade in self.trade_history:
            running += trade["pnl_usd"]
            if running > peak:
                peak = running
            dd = (peak - running) / peak * 100
            if dd > max_dd:
                max_dd = dd

        return {
            "total_trades": len(self.trade_history),
            "winning_trades": len(winning),
            "losing_trades": len(losing),
            "win_rate": len(winning) / len(self.trade_history) * 100 if self.trade_history else 0,
            "total_pnl_usd": round(sum(t["pnl_usd"] for t in self.trade_history), 2),
            "total_pnl_pct": round((self.balance - self.starting_balance) / self.starting_balance * 100, 2),
            "max_drawdown_pct": round(max_dd, 2),
            "profit_factor": round(total_wins / total_losses, 2) if total_losses > 0 else 0,
            "avg_win_usd": round(sum(t["pnl_usd"] for t in winning) / len(winning), 2) if winning else 0,
            "avg_loss_usd": round(sum(t["pnl_usd"] for t in losing) / len(losing), 2) if losing else 0,
            "best_trade_pct": round(max(t["pnl_pct"] for t in self.trade_history), 2) if self.trade_history else 0,
            "worst_trade_pct": round(min(t["pnl_pct"] for t in self.trade_history), 2) if self.trade_history else 0,
            "open_positions": len(self.positions),
            "current_balance": round(self.balance, 2)
        }

    def save_state(self, filepath: Optional[str] = None) -> None:
        """Save paper trading state to JSON file."""
        if filepath is None:
            filepath = self.state_file

        state = {
            "starting_balance": self.starting_balance,
            "balance": self.balance,
            "positions": self.positions,
            "history": self.trade_history,
            "next_trade_id": self.next_trade_id,
            "config": {
                "max_positions": self.max_positions,
                "position_size_pct": self.position_size_pct,
                "default_sl_pct": self.default_sl_pct,
                "default_tp_pct": self.default_tp_pct,
                "trailing_stop_pct": self.trailing_stop_pct
            },
            "saved_at": datetime.now().isoformat()
        }

        with open(filepath, "w") as f:
            json.dump(state, f, indent=2)

    def load_state(self, filepath: Optional[str] = None) -> bool:
        """Load paper trading state from JSON file. Returns True if loaded successfully."""
        if filepath is None:
            filepath = self.state_file

        if not os.path.exists(filepath):
            return False

        try:
            with open(filepath, "r") as f:
                state = json.load(f)

            self.starting_balance = state.get("starting_balance", 10000)
            self.balance = state.get("balance", self.starting_balance)
            self.positions = state.get("positions", [])
            self.trade_history = state.get("history", [])
            self.next_trade_id = state.get("next_trade_id", 1)

            config = state.get("config", {})
            self.max_positions = config.get("max_positions", 1)
            self.position_size_pct = config.get("position_size_pct", 10)
            self.default_sl_pct = config.get("default_sl_pct", 2)
            self.default_tp_pct = config.get("default_tp_pct", 4)
            self.trailing_stop_pct = config.get("trailing_stop_pct", 0)

            return True
        except Exception as e:
            print(f"Error loading state: {e}")
            return False

    def print_status(self) -> None:
        """Print current paper trading status to console."""
        stats = self.get_stats()

        print(f"\n{'='*50}")
        print(f"📋 PAPER TRADING STATUS")
        print(f"{'='*50}")
        print(f"  Balance:      ${stats['current_balance']:,.2f}")
        print(f"  Starting:     ${self.starting_balance:,.2f}")
        print(f"  Total P&L:    ${stats['total_pnl_usd']:+,.2f} ({stats['total_pnl_pct']:+.2f}%)")
        print(f"  Open Pos:     {stats['open_positions']}")
        print(f"{'='*50}")

        if self.positions:
            print(f"\n📌 OPEN POSITIONS:")
            for p in self.positions:
                direction_emoji = "🟢" if p["direction"] == "LONG" else "🔴"
                print(f"  {direction_emoji} {p['direction']} | Entry: ${p['entry_price']:,.0f} | "
                      f"TP: ${p['take_profit']:,.0f} | SL: ${p['stop_loss']:,.0f}")

        if stats["total_trades"] > 0:
            print(f"\n📊 STATS:")
            print(f"  Trades:       {stats['total_trades']} ({stats['winning_trades']}W / {stats['losing_trades']}L)")
            print(f"  Win Rate:     {stats['win_rate']:.1f}%")
            print(f"  Profit Factor: {stats['profit_factor']:.2f}")
            print(f"  Max Drawdown: {stats['max_drawdown_pct']:.2f}%")
            print(f"  Avg Win:      ${stats['avg_win_usd']:+,.2f}")
            print(f"  Avg Loss:     ${stats['avg_loss_usd']:,.2f}")

        print(f"{'='*50}\n")

    def __repr__(self) -> str:
        return f"PaperTrader(balance=${self.balance:.2f}, positions={len(self.positions)}, history={len(self.trade_history)})"


# ============================================================================
# Integration helper for btc-scanner.py
# ============================================================================

def create_paper_trader_from_config(config: dict) -> PaperTrader:
    """Create PaperTrader instance from btc-scanner.conf config dict."""
    return PaperTrader(
        starting_balance=float(config.get("PAPER_STARTING_BALANCE", 10000)),
        max_positions=int(config.get("PAPER_MAX_POSITIONS", 1)),
        position_size_pct=float(config.get("PAPER_POSITION_SIZE_PCT", 10)),
        default_sl_pct=float(config.get("PAPER_DEFAULT_SL_PCT", 2.0)),
        default_tp_pct=float(config.get("PAPER_DEFAULT_TP_PCT", 4.0)),
        trailing_stop_pct=float(config.get("TRAILING_STOP_PCT", 0)),
        state_file=config.get("PAPER_STATE_FILE", "paper_trades.json")
    )


if __name__ == "__main__":
    # Test the paper trader
    pt = PaperTrader(starting_balance=10000)

    # Simulate some trades
    print("Testing PaperTrader...")

    # Open a LONG position
    pos = pt.open_position("LONG", 97000, "2026-02-21 10:00:00", confirmations=3, confidence="MEDIUM")
    print(f"Opened position: {pos['id']}")

    # Simulate price moves
    closed = pt.update_positions(97500, "2026-02-21 11:00:00")
    print(f"Closed: {closed}")

    # Print stats
    stats = pt.get_stats()
    print(f"Stats: {stats}")

    print("\n✓ PaperTrader test passed!")
