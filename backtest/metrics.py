import pandas as pd
import numpy as np
from datetime import datetime

def calculate_advanced_metrics(trades_history, initial_balance=1000.0, timeframe="4h"):
    """
    Υπολογίζει προχωρημένα στατιστικά (Metrics) για το Backtest.
    """
    if not trades_history:
        return _empty_metrics()

    # Μετατροπή σε DataFrame για εύκολους υπολογισμούς
    df = pd.DataFrame(trades_history)
    
    # 1. ΒΑΣΙΚΑ ΣΤΑΤΙΣΤΙΚΑ ΣΥΝΑΛΛΑΓΩΝ
    # --- FIX: Υπολογίζουμε τα δολάρια ΠΡΙΝ το διαχωρισμό! ---
    df['pnl_dollar'] = df['pnl_pct'] / 100 * initial_balance
    
    # Τώρα μπορούμε να διαχωρίσουμε με ασφάλεια
    total_trades = len(df)
    winning_trades = df[df['pnl_pct'] > 0]
    losing_trades = df[df['pnl_pct'] <= 0]
    
    win_rate = (len(winning_trades) / total_trades) * 100 if total_trades > 0 else 0
    
    gross_profit = winning_trades['pnl_dollar'].sum() if not winning_trades.empty else 0
    gross_loss = abs(losing_trades['pnl_dollar'].sum()) if not losing_trades.empty else 0
    
    net_profit = gross_profit - gross_loss
    profit_factor = (gross_profit / gross_loss) if gross_loss > 0 else (99.9 if gross_profit > 0 else 0)
    
    avg_win = winning_trades['pnl_dollar'].mean() if not winning_trades.empty else 0
    avg_loss = abs(losing_trades['pnl_dollar'].mean()) if not losing_trades.empty else 0
    reward_to_risk = (avg_win / avg_loss) if avg_loss > 0 else (99.9 if avg_win > 0 else 0)

    # 2. ΧΡΟΝΙΚΑ ΣΤΑΤΙΣΤΙΚΑ (Διακράτηση & Συνεχόμενες Ήττες)
    df['entry_time'] = pd.to_datetime(df['entry_time'])
    df['exit_time'] = pd.to_datetime(df['exit_time'])
    df['duration'] = (df['exit_time'] - df['entry_time']).dt.total_seconds() / 3600 # Σε ώρες
    
    avg_holding_time = df['duration'].mean() if not df['duration'].isna().all() else 0
    
    # Υπολογισμός Max Consecutive Losses (Συνεχόμενες ήττες)
    max_consec_losses = 0
    current_consec_losses = 0
    for pnl in df['pnl_pct']:
        if pnl <= 0:
            current_consec_losses += 1
            max_consec_losses = max(max_consec_losses, current_consec_losses)
        else:
            current_consec_losses = 0

    # 3. EQUITY CURVE ΚΑΙ DRAWDOWN (Η καμπύλη του υπολοίπου)
    df['cumulative_pnl'] = df['pnl_dollar'].cumsum()
    df['equity'] = initial_balance + df['cumulative_pnl']
    
    # Το μέγιστο (peak) που είχε φτάσει το account μέχρι εκείνη τη στιγμή
    df['peak'] = df['equity'].cummax()
    df['drawdown'] = (df['equity'] - df['peak']) / df['peak'] * 100
    
    max_drawdown = abs(df['drawdown'].min()) if not df['drawdown'].empty else 0
    total_return_pct = (net_profit / initial_balance) * 100

    # 4. ΑΠΟΔΟΣΕΙΣ / SHARPE / SORTINO
    returns = df['pnl_pct'] / 100
    
    first_trade = df['entry_time'].min()
    last_trade = df['exit_time'].max()
    days_passed = (last_trade - first_trade).days if pd.notna(last_trade) and pd.notna(first_trade) else 0
    days_passed = max(1, days_passed) # Αποφυγή διαίρεσης με το 0
    
    # CAGR (Ετήσια Απόδοση)
    years_passed = days_passed / 365.25
    if years_passed > 0 and (total_return_pct/100 + 1) > 0:
        cagr = (((total_return_pct/100 + 1) ** (1/years_passed)) - 1) * 100
    else:
        cagr = 0

    # Θωράκιση τυπικής απόκλισης αν έχουμε < 2 trades
    stdev = returns.std(ddof=0) if len(returns) > 1 else 0
    trade_frequency = len(df) / years_passed if years_passed > 0 else 0
    
    # Sharpe Ratio
    sharpe_ratio = (returns.mean() / stdev * np.sqrt(trade_frequency)) if stdev > 0 else 0
    
    # Sortino Ratio
    downside_returns = returns[returns < 0]
    downside_stdev = downside_returns.std(ddof=0) if len(downside_returns) > 1 else 0
    sortino_ratio = (returns.mean() / downside_stdev * np.sqrt(trade_frequency)) if downside_stdev > 0 else 0

    return {
        "total_return_pct": round(total_return_pct, 2),
        "net_profit": round(net_profit, 2),
        "cagr": round(cagr, 2),
        "max_drawdown": round(max_drawdown, 2),
        "win_rate": round(win_rate, 2),
        "profit_factor": round(profit_factor, 2),
        "avg_win": round(avg_win, 2),
        "avg_loss": round(avg_loss, 2),
        "reward_to_risk": round(reward_to_risk, 2),
        "avg_holding_hours": round(avg_holding_time, 1),
        "total_trades": total_trades,
        "max_consecutive_losses": max_consec_losses,
        "sharpe_ratio": round(sharpe_ratio, 2),
        "sortino_ratio": round(sortino_ratio, 2)
    }

def _empty_metrics():
    return {
        "total_return_pct": 0, "net_profit": 0, "cagr": 0, "max_drawdown": 0,
        "win_rate": 0, "profit_factor": 0, "avg_win": 0, "avg_loss": 0,
        "reward_to_risk": 0, "avg_holding_hours": 0, "total_trades": 0,
        "max_consecutive_losses": 0, "sharpe_ratio": 0, "sortino_ratio": 0
    }