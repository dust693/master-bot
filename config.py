import os
from pathlib import Path
from dotenv import load_dotenv

EXTERNAL_ENV_PATH = ""
if os.path.exists(EXTERNAL_ENV_PATH):
    load_dotenv(dotenv_path=EXTERNAL_ENV_PATH)
else:
    load_dotenv()

# =================================================================
# 1. ΒΑΣΙΚΕΣ ΡΥΘΜΙΣΕΙΣ & API
# =================================================================
BINANCE_API_KEY    = os.getenv("BINANCE_API_KEY", "")
BINANCE_API_SECRET = os.getenv("BINANCE_API_SECRET", "")
BINANCE_BASE_URL   = "https://api.binance.com"

# =================================================================
# 2. ΚΕΝΤΡΙΚΕΣ ΡΥΘΜΙΣΕΙΣ TRADING (Κοινές για Live & Backtest)
# =================================================================
BASE_CURRENCY      = "USDC"       # Αυτό πλέον αλλάζει και από το UI!
TIMEFRAME          = "4h"
STOP_LOSS_PCT      = 0.02
TAKE_PROFIT_PCT    = 0.04
QUOTE_PRECISION    = 2
QUANTITY_PRECISION = 6
TRADING_TYPE       = "spot"  # Επιλογές: "spot" (μόνο Long) ή "margin" (Long & Short)

# =================================================================
# 3. ΡΥΘΜΙΣΕΙΣ BOT & PAPER TRADING
# =================================================================
BOT_MODE               = "paper"
PAPER_BALANCE          = 1000.0
MIN_POSITION_SIZE      = 5.0      # Ελάχιστο μέγεθος θέσης σε USD
MAX_POSITION_SIZE_PCT  = 0.05
MAX_OPEN_POSITIONS     = 5
TRADE_FEE_PCT          = 0.001
DEFAULT_TRADE_SIZE     = 0.01
PAPER_OPEN_POSITIONS   = []
WHITELIST              = []       # Γεμίζει δυναμικά από τον Screener

DEFAULT_BOT_STATE = {
    "is_running": False,
    "is_buying_enabled": True,
    "pair_count": 5,
    "mode": "paper"
}

# =================================================================
# 4. ΡΥΘΜΙΣΕΙΣ ΣΑΡΩΣΗΣ (SCREENER) & BACKTEST
# =================================================================
SCANNER_PAIR_LIMIT     = 3000     # Πόσα ζεύγη να ελέγχει συνολικά από Binance
SCREENER_LOOKBACK_DAYS = 30       # Πόσες μέρες πίσω θα κοιτάει το screener
SCREENER_RUN_HOUR_UTC  = 1        # Τι ώρα (UTC) θα τρέχει το νυχτερινό screener
LOOKBACK_CANDLES       = 1000

BACKTEST_CONFIG = {
    "start_date": "2026-01-01", 
    "end_date": "2026-04-30", 
    "initial_balance": 1000.0,
    "symbols": ['BTCUSDC', 'ETHUSDC', 'SOLUSDC', 'XRPUSDC'], 
    "plot_results": False,
    "cancel": False  # Διακόπτης ασφαλείας για την ακύρωση του τρέχοντος backtest
}

SYMBOL_FILTERS = {
    "min_volume_usdc": 1000000,
    "blacklist": ["USDCUSDC", "USDTUSDT", "BUSDUSDC", "TUSDUSDC", "USD1USDC", "FDUSDUSDC", "EURUSDC"],
    "whitelist": WHITELIST, 
    "max_symbols": 20
}

# =================================================================
# 5. ΤΕΧΝΙΚΕΣ ΡΥΘΜΙΣΕΙΣ (Αρχεία, Καθυστερήσεις, UI)
# =================================================================
BASE_DATA_DIR = Path(r"C:\Users\ioani\Projects\Binance_Historical_Data")
HISTORICAL_DATA_DIR = BASE_DATA_DIR / "Timeframes"
LOGS_DIR = BASE_DATA_DIR / "Logs"
TREND_CACHE_DIR = BASE_DATA_DIR / "trend"

SLEEP_TRADE_LOOP     = 60
SLEEP_IDLE           = 2
SLEEP_API_DELAY      = 0.05
MIN_CANDLES_REQUIRED = 50
KLINES_LIMIT         = 1000

LOG_LEVEL        = "DEBUG"
LOG_TO_FILE      = True
LOG_MAX_BYTES    = 2 * 1024 * 1024
LOG_BACKUP_COUNT = 5

# =================================================================
# INDICATOR_CATEGORIES — Κεντρική Πηγή Αλήθειας για Δείκτες
# =================================================================
# Δομή: { κατηγορία: { όνομα_δείκτη: signal_column } }
#
# Από αυτό το ένα dict παράγονται αυτόματα:
#   - Για app.py    → { κατηγορία: [ονόματα] }       (μόνο τα keys)
#   - Για strategy_manager.py → { δείκτης: signal_col } (μόνο τα values)
#
# Ο ATR δεν παράγει signal column (None) — χρησιμεύει μόνο
# για τον υπολογισμό του ATR-based trailing stop.
# =================================================================
INDICATOR_CATEGORIES = {
    "ΤΑΣΗ (Trend)": {
        "SMA":      "SMA_signal",
        "EMA":      "EMA_signal",
        "DEMA":     "DEMA_signal",
        "TEMA":     "TEMA_signal",
        "WMA":      "WMA_signal",
        "VWAP":     "VWAP_signal",
        "ICHIMOKU": "ICHI_signal",
    },
    "ΟΡΜΗ (Momentum)": {
        "RSI":        "RSI_signal",
        "MACD":       "MACD_pos_signal",
        "STOCHASTIC": "STOCH_signal",
        "CCI":        "CCI_signal",
        "WILLIAMS_R": "WILLIAMS_signal",
        "MFI":        "MFI_signal",
        "ROC":        "ROC_signal",
    },
    "ΜΕΤΑΒΛΗΤΟΤΗΤΑ (Volatility)": {
        "BOLLINGER_BANDS": "BB_signal",
        "ATR":             None,          # Δεν παράγει signal — χρησιμοποιείται μόνο για trailing stop
        "KELTNER":         "KC_signal",
        "DONCHIAN":        "DC_signal",
    },
    "ΟΓΚΟΣ (Volume)": {
        "OBV": "OBV_signal",
        "CMF": "CMF_signal",
        "ADX": "ADX_signal",
    },
}

# =================================================================
# 6. ΡΥΘΜΙΣΕΙΣ ΣΤΡΑΤΗΓΙΚΗΣ & ΔΕΙΚΤΩΝ
# ΡΥΘΜΙΣΕΙΣ TRAILING STOP & RUNTIME STATE
# =================================================================
TRAILING_STOP_CONFIG = {
    "enabled": True,
    "method": "percent",                # Επιλογές: "percent" ή "atr"
    "trail_pct": 0.03,                  # Ποσοστό trailing (π.χ. 3% -> 0.03)
    "activation_pct": 0.01,             # Ποσοστό ενεργοποίησης κέρδους (π.χ. 1% -> 0.01)
    "atr_multiplier": 2.0,              # Πολλαπλασιαστής ATR (χρησιμοποιείται αν method="atr")
    "use_alongside_fixed_sl": True      # Παράλληλη χρήση με το σταθερό Stop Loss ως "πάτωμα"
}

# Runtime State: Λεξικό για την καταγραφή των ανώτατων τιμών (peaks)
# των ανοιχτών θέσεων στο Paper Trading (π.χ. {"BTCUSDC": 68500.0})
PAPER_TRAILING_STOPS = {}

STRATEGY_CONFIG = {
    "SMA": {"enabled": True, "fast_period": 10, "slow_period": 50, "signal": "crossover", "signal_options": {"crossover": "Crossover (Fast πάνω από Slow)", "price_above": "Τιμή πάνω από Slow SMA", "price_above_fast": "Τιμή πάνω από Fast SMA"}},
    "EMA": {"enabled": True, "fast_period": 9, "slow_period": 21, "signal": "crossover", "signal_options": {"crossover": "Crossover (Fast πάνω από Slow)", "price_above": "Τιμή πάνω από Slow EMA", "price_above_fast": "Τιμή πάνω από Fast EMA"}},
    "DEMA": {"enabled": True, "period": 21, "signal": "crossover", "slope_period": 3, "signal_options": {"price_above": "Τιμή πάνω από DEMA", "slope": "Θετική Κλίση (Ανοδική)", "crossover": "Τιμή διασχίζει πάνω από DEMA"}},
    "TEMA": {"enabled": True, "period": 21, "signal": "price_above", "slope_period": 3, "signal_options": {"price_above": "Τιμή πάνω από TEMA", "slope": "Θετική Κλίση (Ανοδική)"}},
    "WMA": {"enabled": True, "fast_period": 9, "slow_period": 21, "signal": "crossover", "signal_options": {"crossover": "Crossover (Fast πάνω από Slow)", "price_above": "Τιμή πάνω από Slow WMA"}},
    "VWAP": {"enabled": True, "signal": "price_above", "signal_options": {"price_above": "Τιμή πάνω από VWAP (Bullish)", "price_below": "Τιμή κάτω από VWAP (Bearish)"}},
    "ICHIMOKU": {"enabled": True, "tenkan": 9, "kijun": 26, "senkou_b": 52, "signal": "cloud_breakout", "signal_options": {"cloud_breakout": "Breakout (Τιμή πάνω από Cloud)", "tenkan_kijun_cross": "Crossover (Tenkan πάνω από Kijun)", "cloud_twist": "Cloud Twist (Senkou A πάνω από B)"}},
    "RSI": {"enabled": True, "period": 14, "oversold": 30, "overbought": 70, "confirmation_candles": 1},
    "MACD": {"enabled": True, "fast": 12, "slow": 26, "signal_line": 9, "signal_type": "crossover", "signal_options": {"crossover": "Crossover (MACD πάνω από Signal)", "histogram_zero": "Ιστόγραμμα διασχίζει το 0 (Zero Cross)", "histogram_increasing": "Ιστόγραμμα αυξάνεται"}},
    "STOCHASTIC": {"enabled": True, "k_period": 14, "d_period": 3, "smooth_k": 3, "oversold": 20, "overbought": 80},
    "CCI": {"enabled": True, "period": 20, "oversold": -100, "overbought": 100},
    "WILLIAMS_R": {"enabled": True, "period": 14, "oversold": -80, "overbought": -20},
    "MFI": {"enabled": True, "period": 14, "oversold": 20, "overbought": 80},
    "ROC": {"enabled": True, "period": 12, "threshold": 0, "signal": "crossover_zero", "signal_options": {"above_threshold": "Πάνω από Threshold", "crossover_zero": "Διασταύρωση του Μηδενός"}},
    "BOLLINGER_BANDS": {"enabled": True, "period": 20, "std_dev": 2.0, "signal": "breakout", "squeeze_threshold": 0.015, "signal_options": {"breakout": "Breakout (Τιμή πάνω από Upper Band)", "mean_reversion": "Mean Reversion (Αγορά στο Lower Band)", "squeeze": "Squeeze (Στένωση Ζωνών)"}},
    "ATR": {"enabled": True, "period": 14, "multiplier": 1.5},
    "KELTNER": {"enabled": True, "period": 20, "atr_multiplier": 2.0, "signal": "breakout", "signal_options": {"breakout": "Breakout (Τιμή πάνω από Upper Band)", "mean_reversion": "Mean Reversion (Αγορά στο Lower Band)"}},
    "DONCHIAN": {"enabled": True, "period": 20, "signal": "breakout", "signal_options": {"breakout": "Breakout (Τιμή σπάει το Highest High)"}},
    "OBV": {"enabled": True, "signal": "sma_crossover", "trend_period": 10, "signal_options": {"trend": "Trend (Σταθερά Ανοδικό OBV)", "sma_crossover": "Crossover (OBV διασχίζει τον κινητό του μέσο)"}},
    "CMF": {"enabled": True, "period": 20, "threshold": 0},
    "ADX": {"enabled": True, "period": 14, "threshold": 25, "signal": "strong_trend", "signal_options": {"strong_trend": "Ισχυρή Τάση (ADX > Threshold + Bullish DI)"}},
    "COMBINATION_LOGIC": "AND"
}

# =================================================================
# PERFORMANCE FILTERS (Κριτήρια ανοίγματος / αποδοχής στρατηγικής)
# =================================================================

'''
1. Απόδοση & Κερδοφορία
Μέτρο                   Minimum για live             Άνετο                      Σημείωση
Total Return %            >0% (προφανώς)          >15% ανά έτος       Μόνο αν έχεις αρκετά trades
CAGR                      >10%                    >20%                Κάτω από 10% δεν αξίζει vs buy & hold
Μέσο Κέρδος/Trade         >0.3% μετά fees         >0.8%               Πρέπει να καλύπτει fees + slippage

2. Ρίσκο & Μεταβλητότητα
Μέτρο                   Αποκλεισμός                 Minimum                 Άνετο
Max Drawdown %            >35%                        <20%                  <12%
Sharpe Ratio              <0.5                        >1.0                  >1.5
Sortino Ratio             <0.8                        >1.5                  >2.0
Exposure %                >80%                        <60%                  <40%

3. Ποιότητα Συναλλαγών
Μέτρο                   Αποκλεισμός                 Minimum                 Άνετο
Win Rate %                <40%                        >45%                   >55%
Profit Factor             <1.2                        >1.5                   >2.0
Avg Win / Avg Loss        <0.8                        >1.2                   >1.5
Αριθμός Trades            <30                         >50                    >100
Max Consecutive Losses    >8                          <6                      <4
'''

PERFORMANCE_FILTERS = {
    "min_cagr": 0.1,                  # Ετήσια Απόδοση > 20%
    "min_avg_trade_pct": 0.4,         # Μέσο κέρδος ανά trade > 0.8%
    "max_drawdown": 0.3,              # Μέγιστο Drawdown < 12%
    "min_sharpe": 0.6,                # Sharpe Ratio > 1.5
    "min_sortino": 1.0,               # Sortino Ratio > 2.0
    "min_win_rate": 0.4,              # Ποσοστό Επιτυχίας > 55%
    "min_profit_factor": 1.3,         # Profit Factor > 2.0
    "min_reward_to_risk": 0.9,        # Avg Win / Avg Loss > 1.5
    "min_trades": 10,                 # Ελάχιστος αριθμός συναλλαγών
    "max_consecutive_losses": 6       # Συνεχόμενες ήττες αυστηρά < 6 (άρα max 5)
}