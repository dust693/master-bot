# =============================================================================
# screener_filters.py
# =============================================================================
# Σκοπός: Εφαρμόζει τα "αυστηρά κριτήρια ποιότητας" (config.PERFORMANCE_FILTERS)
# σε ΕΝΑ ζεύγος (symbol) του Backtest, ώστε να αποφασιστεί αν αξίζει να μπει
# στα "Golden Pairs" (config.WHITELIST).
#
# Πριν από αυτό το fix, το backtest/engine.py έκανε ΜΟΝΟ έναν έλεγχο:
#       if pair_total_pnl_pct > 0:   # δηλ. απλά "βγήκε κερδοφόρο, οτιδήποτε"
# και ΑΓΝΟΟΥΣΕ ΕΝΤΕΛΩΣ το config.PERFORMANCE_FILTERS (CAGR, Sharpe, Sortino,
# Drawdown, Win Rate, Profit Factor κλπ.) που ήταν ορισμένο στο config.py
# αλλά ΔΕΝ χρησιμοποιούνταν ΠΟΥΘΕΝΑ.
#
# Τώρα: ΟΛΑ τα κατώφλια έρχονται ΑΠΟΚΛΕΙΣΤΙΚΑ από config.PERFORMANCE_FILTERS
# (κεντρικός εγκέφαλος) - τίποτα hardcoded εδώ.
# =============================================================================

import config
from backtest.metrics import calculate_advanced_metrics


def evaluate_pair_performance(trades: list, initial_balance: float, timeframe: str) -> dict:
    """
    Υπολογίζει τα advanced metrics (CAGR, Sharpe, Sortino, Drawdown κλπ.) για
    τις συναλλαγές ΕΝΟΣ ΜΟΝΟ ζεύγους (π.χ. BTCUSDC), χρησιμοποιώντας την ίδια
    συνάρτηση calculate_advanced_metrics() που χρησιμοποιείται και για το
    συνολικό αποτέλεσμα του backtest.

    Επιστρέφει το dict των metrics, προσθέτοντας επιπλέον το κλειδί
    "passed_filters": True/False, που δείχνει αν το ζεύγος περνάει τα
    κριτήρια του config.PERFORMANCE_FILTERS.
    """
    metrics = calculate_advanced_metrics(trades, initial_balance=initial_balance, timeframe=timeframe)
    metrics["passed_filters"] = _passes_performance_filters(metrics)
    return metrics


def _passes_performance_filters(metrics: dict) -> bool:
    """
    Συγκρίνει τα metrics ενός ζεύγους με τα κατώφλια του
    config.PERFORMANCE_FILTERS. Το ζεύγος θεωρείται "Golden" μόνο αν
    ΟΛΑ τα κριτήρια περνούν (λογικό AND).

    Σημείωση μονάδων: το calculate_advanced_metrics() επιστρέφει ποσοστά
    (cagr, max_drawdown, win_rate) ως ΑΚΕΡΑΙΟΥΣ αριθμούς (π.χ. 12.5 = 12.5%),
    ενώ το config.PERFORMANCE_FILTERS τα ορίζει ως δεκαδικά (0.1 = 10%) -
    όπως ακριβώς ορίζονται και τα STOP_LOSS_PCT/TAKE_PROFIT_PCT στο config.
    Γι' αυτό διαιρούμε με 100 πριν τη σύγκριση.
    """
    filters = config.PERFORMANCE_FILTERS

    total_trades = metrics["total_trades"]
    if total_trades == 0:
        return False

    # Μέσο κέρδος ανά συναλλαγή (%)
    avg_trade_pct = metrics["total_return_pct"] / total_trades

    checks = {
        "min_cagr": (metrics["cagr"] / 100) >= filters["min_cagr"],
        "min_avg_trade_pct": (avg_trade_pct / 100) >= filters["min_avg_trade_pct"],
        "max_drawdown": (metrics["max_drawdown"] / 100) <= filters["max_drawdown"],
        "min_sharpe": metrics["sharpe_ratio"] >= filters["min_sharpe"],
        "min_sortino": metrics["sortino_ratio"] >= filters["min_sortino"],
        "min_win_rate": (metrics["win_rate"] / 100) >= filters["min_win_rate"],
        "min_profit_factor": metrics["profit_factor"] >= filters["min_profit_factor"],
        "min_reward_to_risk": metrics["reward_to_risk"] >= filters["min_reward_to_risk"],
        "min_trades": total_trades >= filters["min_trades"],
        "max_consecutive_losses": metrics["max_consecutive_losses"] < filters["max_consecutive_losses"],
    }

    return all(checks.values())
