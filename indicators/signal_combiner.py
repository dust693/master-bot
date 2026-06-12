# =============================================================================
# signal_combiner.py
# =============================================================================
# Συνδυάζει τα επιμέρους signal columns που παράγει το technical_analysis.py
# (π.χ. SMA_signal, RSI_signal, MACD_pos_signal, ...) σε ΕΝΑ τελικό 'signal'
# column, για ΚΑΘΕ γραμμή (κερί) του DataFrame.
#
# ΓΙΑΤΙ ΥΠΑΡΧΕΙ ΑΥΤΟ ΤΟ ΑΡΧΕΙΟ:
#   Πριν, η λογική συνδυασμού σημάτων (AND/OR/MAJORITY) υπήρχε ΜΟΝΟ μέσα στο
#   StrategyManager._combine_signals και δούλευε ΜΟΝΟ για το τελευταίο κερί
#   (live/paper trading). Το Backtest Engine χρειάζεται την ΙΔΙΑ λογική αλλά
#   για ΟΛΑ τα κεριά μαζί (vectorized), ώστε να κάνει candle-by-candle
#   προσομοίωση. Επειδή αυτή η δεύτερη υλοποίηση δεν υπήρχε, το backtest
#   έψαχνε μια στήλη 'signal' που ΔΕΝ υπήρχε ποτέ -> καμία θέση δεν άνοιγε.
#
#   Για να μην υπάρχουν 2 ξεχωριστές (και πιθανόν αποκλίνουσες) υλοποιήσεις
#   της ίδιας λογικής, η συνάρτηση συνδυασμού ζει ΕΔΩ - μία φορά - και τη
#   χρησιμοποιεί το technical_analysis.py για ΟΛΑ τα κεριά (backtest + live).
#
# ΛΟΓΙΚΗ ΣΥΝΔΥΑΣΜΟΥ (config.STRATEGY_CONFIG["COMBINATION_LOGIC"]):
#   - "AND"      : ΟΛΟΙ οι δείκτες που έδωσαν σήμα (όχι HOLD) πρέπει να
#                  συμφωνούν στην ΙΔΙΑ κατεύθυνση (όλοι BUY ή όλοι SELL).
#   - "OR"       : Νικά η πλευρά (BUY/SELL) με τις περισσότερες "ψήφους".
#   - "MAJORITY" : Όπως το OR, αλλά η νικήτρια πλευρά πρέπει να ξεπερνά το
#                  50% του ΣΥΝΟΛΟΥ των ενεργών δεικτών (όχι μόνο των "valid").
# =============================================================================

import pandas as pd
import config

# -----------------------------------------------------------------------
# SIGNAL_COLUMNS: { όνομα_δείκτη: στήλη_σήματος }
# -----------------------------------------------------------------------
# Παράγεται αυτόματα από το config.INDICATOR_CATEGORIES, που είναι η ΚΕΝΤΡΙΚΗ
# πηγή αλήθειας για όλους τους δείκτες (config = κεντρικός εγκέφαλος).
# Ο ATR εξαιρείται (signal column = None) γιατί δεν παράγει σήμα BUY/SELL.
SIGNAL_COLUMNS = {
    indicator_name: signal_col
    for category in config.INDICATOR_CATEGORIES.values()
    for indicator_name, signal_col in category.items()
    if signal_col is not None
}


def add_combined_signal(df: pd.DataFrame, strategy_cfg: dict) -> pd.DataFrame:
    """
    Προσθέτει τη στήλη 'signal' στο df, συνδυάζοντας τα *_signal των ΕΝΕΡΓΩΝ
    δεικτών για ΚΑΘΕ γραμμή, σύμφωνα με το strategy_cfg["COMBINATION_LOGIC"].

    Τιμές 'signal':
        +1 = BUY (Άνοιγμα Long / Κλείσιμο Short)
        -1 = SELL (Κλείσιμο Long / Άνοιγμα Short)
         0 = HOLD / Ουδέτερο

    Λειτουργεί τόσο για backtest (όλες οι γραμμές) όσο και για live
    (χρησιμοποιείται μόνο η τελευταία γραμμή από τον StrategyManager).
    """

    # 1. Ποιες στήλες σήματος αντιστοιχούν σε ΕΝΕΡΓΟΥΣ δείκτες ΚΑΙ υπάρχουν
    #    πραγματικά στο df (ένας δείκτης μπορεί να έχει αποτύχει στον υπολογισμό).
    active_signal_cols = []
    for indicator_name, signal_col in SIGNAL_COLUMNS.items():
        ind_cfg = strategy_cfg.get(indicator_name, {})
        if isinstance(ind_cfg, dict) and ind_cfg.get("enabled", False) and signal_col in df.columns:
            active_signal_cols.append(signal_col)

    df['signal'] = 0

    if not active_signal_cols:
        # Κανένας ενεργός δείκτης -> δεν υπάρχει σήμα, όλα 0 (HOLD)
        return df

    # 2. Πίνακας με τα επιμέρους σήματα. fillna(0) -> τα αρχικά κεριά (όπου ο
    #    δείκτης δεν έχει ακόμα υπολογιστεί, π.χ. NaN λόγω rolling window)
    #    μετράνε ως HOLD και δεν επηρεάζουν τον συνδυασμό.
    signals_matrix = df[active_signal_cols].fillna(0).astype(int)

    total_indicators = len(active_signal_cols)
    buy_count = (signals_matrix == 1).sum(axis=1)
    sell_count = (signals_matrix == -1).sum(axis=1)
    valid_count = (signals_matrix != 0).sum(axis=1)  # δείκτες που έδωσαν BUY ή SELL (όχι HOLD)

    combination = strategy_cfg.get("COMBINATION_LOGIC", "AND")

    if combination == "AND":
        # Πρέπει να υπάρχει τουλάχιστον ένα σήμα (valid_count > 0) ΚΑΙ ΟΛΟΙ
        # όσοι έδωσαν σήμα να συμφωνούν στην ίδια κατεύθυνση.
        buy_mask = (valid_count > 0) & (buy_count == valid_count) & (sell_count == 0)
        sell_mask = (valid_count > 0) & (sell_count == valid_count) & (buy_count == 0)

    elif combination == "OR":
        # Νικά όποια πλευρά έχει τις περισσότερες "ψήφους".
        buy_mask = (valid_count > 0) & (buy_count > sell_count)
        sell_mask = (valid_count > 0) & (sell_count > buy_count)

    elif combination == "MAJORITY":
        # Όπως το OR, αλλά η νικήτρια πλευρά πρέπει να ξεπερνά το 50% του
        # ΣΥΝΟΛΟΥ των ενεργών δεικτών (πιο αυστηρό από το OR).
        buy_mask = (buy_count > sell_count) & (buy_count > total_indicators * 0.5)
        sell_mask = (sell_count > buy_count) & (sell_count > total_indicators * 0.5)

    else:
        # Άγνωστη τιμή COMBINATION_LOGIC -> ασφαλής επιλογή: κανένα σήμα.
        buy_mask = pd.Series(False, index=df.index)
        sell_mask = pd.Series(False, index=df.index)

    df.loc[buy_mask, 'signal'] = 1
    df.loc[sell_mask, 'signal'] = -1

    return df
