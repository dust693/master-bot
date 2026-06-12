import os
import time
import copy
import threading
from datetime import datetime, timedelta, timezone
from flask import Flask, render_template, request, jsonify
import config
from utils.logger import change_log_level, logger
from exchange.binance_client import BinanceExchange
from strategies.strategy_manager import StrategyManager
from strategies.settings_updater import apply_core_settings, apply_indicator_settings, apply_trailing_stop_settings
from strategies.position_manager import try_open_position, try_close_position
from backtest.engine import run_backtester
from backtest.trend_cache import TrendCache

app = Flask(__name__)
DEFAULT_STRATEGY_CONFIG = copy.deepcopy(config.STRATEGY_CONFIG)

exchange = BinanceExchange()
manager = StrategyManager()
trend_cache = TrendCache(exchange)

def get_max_required_period():
    """Επιστρέφει το μέγιστο period από όλους τους ενεργοποιημένους δείκτες."""
    max_period = 50   # default ασφαλείας
    for ind_name, params in manager.strategy_cfg.items():
        if isinstance(params, dict) and params.get("enabled", False):
            for key in ['period', 'slow_period', 'fast_period', 'tenkan', 'kijun', 'senkou_b']:
                if key in params:
                    max_period = max(max_period, params[key])
    return max_period

# Αρχικό κλείσιμο δεικτών
for ind in manager.strategy_cfg:
    if isinstance(manager.strategy_cfg[ind], dict): manager.strategy_cfg[ind]['enabled'] = False
manager.active_indicators = []

# Η κατηγοριοποίηση και το αρχικό state πλέον έρχονται από το config.
# Το config.INDICATOR_CATEGORIES έχει δομή {κατηγορία: {δείκτης: signal_col}}.
# Εδώ κρατάμε μόνο τα ονόματα δεικτών ανά κατηγορία για το Jinja2 template —
# το UI δεν χρειάζεται τα signal columns.
INDICATOR_CATEGORIES = {
    cat: list(inds.keys())
    for cat, inds in config.INDICATOR_CATEGORIES.items()
}
BOT_STATE = config.DEFAULT_BOT_STATE.copy()


def force_exit_positions():
    logger.warning("!!! FORCE EXIT ΕΝΕΡΓΟΠΟΙΗΘΗΚΕ !!!")
    try:
        open_positions = exchange.get_open_positions()
        for symbol in open_positions:
            exchange.place_order(symbol, "SELL", quantity=config.DEFAULT_TRADE_SIZE)
            logger.info(f"Force Exit: Πουλήθηκε το {symbol}")

        # ΔΙΟΡΘΩΣΗ (fix #6): Καθαρισμός του OPEN_POSITIONS_DATA (κεντρικό
        # record entry/highest/stop_price) τρέχει ΠΑΝΤΑ, σε Paper ΚΑΙ σε
        # Live Mode, αφού πλέον γεμίζει και στα δύο modes.
        config.OPEN_POSITIONS_DATA.clear()

        if config.BOT_MODE == "paper" and hasattr(config, 'PAPER_OPEN_POSITIONS'):
            config.PAPER_OPEN_POSITIONS = []

    except Exception as e:
        logger.error(f"Σφάλμα κατά την αναγκαστική έξοδο: {e}")

# Αρχική λίστα για τα Golden Pairs
config.WHITELIST = []


def daily_screener_task():
    """Τρέχει σιωπηλά το Backtest (τελευταίες 30 μέρες) και βρίσκει τα Golden Pairs"""
    logger.info("🔍 [SCREENER] Ξεκινάει η σάρωση αγοράς για να βρεθούν τα Golden Pairs...")

    now = datetime.now()
    # Παίρνουμε τις μέρες από το config
    thirty_days_ago = now - timedelta(days=config.SCREENER_LOOKBACK_DAYS)

    # Δημιουργούμε ένα εικονικό "form" σαν να το έστειλε ο χρήστης από το UI
    mock_form = {
        "start_date": thirty_days_ago.strftime("%Y-%m-%d"),
        "end_date": now.strftime("%Y-%m-%d"),
        "pair_count": config.SCANNER_PAIR_LIMIT  # Ψάξε όσο χρειαστεί!
    }

    try:
        res = run_backtester(mock_form, exchange, manager)
        if "error" in res and not res.get("pair_details"):
            logger.warning(f"⚠️ [SCREENER] Δεν βρέθηκε κανένα ζεύγος που να περνάει τα αυστηρά κριτήρια!")
            config.WHITELIST = []
            return

        # Βρίσκουμε ποια νομίσματα επέστρεψε η μηχανή ως "Golden"
        golden_pairs = list(res.get("pair_details", {}).keys())
        config.WHITELIST = golden_pairs
        logger.info(f"✅ [SCREENER] Ολοκληρώθηκε! Βρέθηκαν {len(golden_pairs)} Golden Pairs: {golden_pairs}")
    except Exception as e:
        logger.error(f"❌ [SCREENER] Σφάλμα κατά τη σάρωση: {e}")


def screener_scheduler_loop():
    """Ελέγχει την ώρα (Internet) και τρέχει τον screener στη 01:00 UTC"""
    has_run_today = False
    last_run_day = None

    # Εκτελούμε το Screener μία φορά αμέσως μόλις ανοίξει το Bot
    daily_screener_task()

    while True:
        try:
            utc_now = exchange.get_server_time()
            current_day = utc_now.day

            # Reset του flag την επόμενη μέρα
            if current_day != last_run_day:
                has_run_today = False

            # Αν είναι 01:00 UTC ακριβώς (ή 01:01 κλπ μέσα στην ίδια ώρα) και δεν έχει τρέξει σήμερα
            if utc_now.hour == config.SCREENER_RUN_HOUR_UTC and not has_run_today:
                daily_screener_task()
                has_run_today = True
                last_run_day = current_day

        except Exception as e:
            logger.error(f"Σφάλμα στο Scheduler: {e}")

        time.sleep(config.SLEEP_TRADE_LOOP)


# Εκκίνηση του αυτόνομου Screener Thread (παράλληλα με το Trade Loop)
threading.Thread(target=screener_scheduler_loop, daemon=True).start()


def auto_trade_loop():
    while True:
        if BOT_STATE["is_running"] and manager.active_indicators:
            try:
                # --- Η ΛΥΣΗ ΤΩΝ ΕΠΑΓΓΕΛΜΑΤΙΩΝ ---
                # To Bot δεν κατεβάζει πλέον όλη την αγορά στα τυφλά.
                # Ελέγχει ΜΟΝΟ τα "Χρυσά Ζεύγη" (Golden Pairs) που βρήκε ο νυχτερινός Screener.
                top_pairs = config.WHITELIST

                open_positions = exchange.get_open_positions()

                if not top_pairs and not open_positions:
                    # Αν δεν υπάρχει κανένα Golden Pair ΚΑΙ δεν έχουμε ανοιχτές θέσεις, κοιμόμαστε!
                    time.sleep(60)
                    continue

                # Αν έχουμε ανοιχτές θέσεις που ΔΕΝ είναι πλέον στη Whitelist, πρέπει να τις
                # ελέγξουμε ούτως ή άλλως (για να κλείσουν με SL/TP), άρα τις προσθέτουμε στη λίστα.
                pairs_to_check = list(set(top_pairs + open_positions))

                # DEBUG: Τρέχουσα τάση BTC (UPTREND/DOWNTREND), υπολογισμένη ΜΙΑ φορά ανά
                # κύκλο (ίδια για όλα τα symbols) - πριν από οποιοδήποτε άνοιγμα θέσης.
                # ΔΕΝ μπλοκάρει αγορές, είναι ΜΟΝΟ ενημερωτικό (βλ. config.BTC_TREND_CONFIG).
                btc_trend = trend_cache.get_current_btc_trend()
                if btc_trend["uptrend"] is not None:
                    state = "UPTREND 🟢" if btc_trend["uptrend"] else "DOWNTREND 🔴"
                    logger.debug(
                        f"📈 [BTC TREND] {state} "
                        f"(daily_bullish={btc_trend['daily_bullish']}, 4h_bullish={btc_trend['4h_bullish']})"
                    )

                for symbol in pairs_to_check:
                    end_date = datetime.now(timezone.utc)
                    max_period = get_max_required_period()

                    # Πόσο ιστορικό (σε ημέρες) χρειάζεται για να υπάρχουν αρκετά candles
                    # ώστε να υπολογιστούν σωστά οι δείκτες (buffer x2 για ασφάλεια).
                    days_per_candle = config.TIMEFRAME_TO_DAYS.get(config.TIMEFRAME, 1)
                    lookback_days = max_period * days_per_candle * 2
                    start_date = end_date - timedelta(days=lookback_days)

                    df = exchange.get_candles_cached(
                        symbol,
                        config.TIMEFRAME,
                        start_date.strftime("%Y-%m-%d"),
                        end_date.strftime("%Y-%m-%d")
                    )
                    if df is None or df.empty or len(df) < 50:
                        continue

                    signal = manager.analyze(df, symbol)
                    current_price = float(df.iloc[-1]['close'])
                    current_high = float(df.iloc[-1]['high'])

                    # === ΔΙΑΧΕΙΡΙΣΗ ΘΕΣΗΣ (BUY / SELL & TAKE PROFIT / STOP / TRAILING) ===
                    # Όλη η λογική ανοίγματος/κλεισίματος θέσης ζει πλέον στο
                    # strategies/position_manager.py (config = κεντρικός εγκέφαλος
                    # για SL %, TP %, Trailing Stop, μεγέθη θέσεων κλπ.)
                    if symbol in open_positions:
                        try_close_position(exchange, manager, symbol, df, signal, current_price, current_high)
                    else:
                        try_open_position(exchange, symbol, signal, current_price, current_high, open_positions, BOT_STATE)

                time.sleep(config.SLEEP_TRADE_LOOP)
            except Exception as e:
                logger.error(f"Σφάλμα στο auto_trade_loop: {e}")
                time.sleep(10) # Εδώ κρατάμε το 10άρι, είναι safe για error fallback
        else:
            time.sleep(config.SLEEP_IDLE)

threading.Thread(target=auto_trade_loop, daemon=True).start()

@app.route('/stop_buys', methods=['POST'])
def stop_buys():
    BOT_STATE["is_buying_enabled"] = False
    logger.warning("⚠️ Ο χρήστης ΣΤΑΜΑΤΗΣΕ τις νέες αγορές!")
    return jsonify({"msg": "Οι ΝΕΕΣ αγορές σταμάτησαν. Το bot πλέον μόνο πουλάει."})

@app.route('/force_exit', methods=['POST'])
def force_exit():
    BOT_STATE["is_buying_enabled"] = False
    force_exit_positions()
    return jsonify({"msg": "Όλες οι ανοιχτές θέσεις έκλεισαν αμέσως (Force Exit)!"})

@app.route('/shutdown', methods=['POST'])
def shutdown():
    force_exit_positions()
    # Κάνουμε παύση 1 δευτερόλεπτο πριν κλείσει το Python για να προλάβει να απαντήσει στο UI
    def kill_server():
        time.sleep(1)
        os._exit(0)
    threading.Thread(target=kill_server).start()
    return jsonify({"msg": "Το σύστημα τερματίζεται με ασφάλεια..."})

@app.route('/')
def index():
    # Πακετάρουμε τις τρέχουσες γενικές ρυθμίσεις για να τις στείλουμε στο UI
    global_cfg = {
        "timeframe": config.TIMEFRAME,
        "sl_pct": config.STOP_LOSS_PCT * 100,
        "tp_pct": config.TAKE_PROFIT_PCT * 100,
        "logic": manager.strategy_cfg.get('COMBINATION_LOGIC', 'AND'),
        "base_currency": config.BASE_CURRENCY,  # Προστέθηκε το base currency
        "trading_type": getattr(config, 'TRADING_TYPE', 'spot')
    }
    return render_template('index.html',
                           strategy_cfg=manager.strategy_cfg,
                           categories=config.INDICATOR_CATEGORIES,
                           defaults=DEFAULT_STRATEGY_CONFIG,
                           ts_cfg=config.TRAILING_STOP_CONFIG,  # Στέλνουμε το Trailing Stop config
                           global_cfg=global_cfg)               # Στέλνουμε τα General configs

@app.route('/toggle_bot', methods=['POST'])
def toggle_bot():
    action = request.form.get('action')
    logger.debug(f"🕹️ [UI ACTION] Toggle Bot πατήθηκε με ενέργεια: '{action}'")

    if action == 'start':
        BOT_STATE['is_running'] = True
        logger.info("▶️ [BOT STATE] Το Trading Bot ΕΚΚΙΝΗΘΗΚΕ.")
    elif action == 'stop':
        BOT_STATE['is_running'] = False
        logger.info("⏸️ [BOT STATE] Το Trading Bot ΣΤΑΜΑΤΗΣΕ.")

    logger.debug(f"📊 [CURRENT BOT STATE] Complete State: {BOT_STATE}")
    return jsonify({"msg": f"Το bot άλλαξε κατάσταση σε: {action}", "state": BOT_STATE})

@app.route('/set_mode', methods=['POST'])
def set_mode():
    mode = request.form.get('mode', 'paper')
    logger.debug(f"🔄 [UI ACTION] Αλλαγή Mode λειτουργίας σε: '{mode}'")

    config.BOT_MODE = mode
    BOT_STATE['mode'] = mode

    logger.info(f"💼 [MODE CHANGED] Το σύστημα πλέον εκτελείται σε: {mode.upper()} mode.")
    return jsonify({"msg": f"Mode άλλαξε σε {mode}"})

# --- ΠΡΟΣΘΗΚΗ: Νέα διαδρομή για το Base Currency ---
@app.route('/set_base_currency', methods=['POST'])
def set_base_currency():
    new_currency = request.form.get('currency', 'USDC').upper()
    config.BASE_CURRENCY = new_currency
    logger.info(f"💱 Το Νόμισμα Βάσης (Base Currency) άλλαξε σε: {new_currency}")
    return jsonify({"msg": f"Το νόμισμα άλλαξε επιτυχώς σε {new_currency}!\nΤώρα το Bot θα ψάχνει ζεύγη {new_currency}."})


@app.route('/get_logs', methods=['GET'])
def get_logs():
    """Διαβάζει δυναμικά το τρέχον αρχείο καταγραφής"""
    from utils.logger import current_log_file

    if not os.path.exists(current_log_file):
        return jsonify({"logs": "Το αρχείο καταγραφής δεν έχει δημιουργηθεί ακόμα."})
    try:
        with open(current_log_file, "r", encoding="utf-8", errors="ignore") as f:
            lines = f.readlines()
        return jsonify({"logs": "".join(lines[-150:])})
    except Exception as e:
        return jsonify({"logs": f"Σφάλμα ανάγνωσης αρχείου καταγραφής: {str(e)}"})

@app.route('/update_strategy', methods=['POST'])
def update_strategy():
    """
    Δέχεται τη φόρμα ρυθμίσεων από το UI και ενημερώνει το config.py
    (= κεντρικός εγκέφαλος) μέσω των helper συναρτήσεων του
    strategies/settings_updater.py:
      1. apply_core_settings        -> Timeframe, Stop Loss %, Take Profit %
      2. apply_indicator_settings   -> Combination Logic + ρυθμίσεις δεικτών
      3. apply_trailing_stop_settings -> TRAILING_STOP_CONFIG
    """
    logger.debug("💾 [UI ACTION] Λήφθηκε αίτημα αποθήκευσης νέων ρυθμίσεων στρατηγικής.")

    apply_core_settings(request.form)
    apply_indicator_settings(request.form, manager)
    apply_trailing_stop_settings(request.form)

    return jsonify({"msg": "Στρατηγική + Trailing Stop Ενημερώθηκαν Επιτυχώς!"})

@app.route('/backtest', methods=['POST'])
def route_backtest():
    """Δρομολογεί το αίτημα στο backtest engine"""

    # ΠΡΟΣΘΗΚΗ: Καθαρίζουμε τον διακόπτη πριν ξεκινήσει το νέο backtest
    config.BACKTEST_CONFIG['cancel'] = False
    logger.info("🚀 Ξεκινάει νέο Backtest...")

    form_data = request.form.to_dict()
    # Συνέχεια του κώδικά σου...
    result = run_backtester(form_data, exchange, manager)

    return jsonify(result)

@app.route('/cancel_backtest', methods=['POST'])
def cancel_backtest():
    """Δέχεται την εντολή ακύρωσης από το UI"""
    logger.warning("🚨 Λήφθηκε αίτημα ακύρωσης του Backtest από το UI!")
    config.BACKTEST_CONFIG['cancel'] = True  # Ενεργοποίηση της ακύρωσης στο config σου
    return jsonify({"msg": "Το Backtest ακυρώθηκε επιτυχώς."})

@app.route('/set_trading_type', methods=['POST'])
def set_trading_type():
    t_type = request.form.get('trading_type', 'spot').lower()
    if t_type in ['spot', 'margin']:
        config.TRADING_TYPE = t_type
        logger.info(f"🔄 Ο τύπος αγοράς άλλαξε σε: {t_type.upper()}")
        return jsonify({"msg": f"Επιτυχής αλλαγή σε {t_type.upper()} Trading!"})
    return jsonify({"error": "Μη έγκυρος τύπος αγοράς."}), 400

if __name__ == '__main__':
    app.run(debug=True, use_reloader=False)