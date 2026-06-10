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
from backtest.engine import run_backtester

app = Flask(__name__)
DEFAULT_STRATEGY_CONFIG = copy.deepcopy(config.STRATEGY_CONFIG)

exchange = BinanceExchange()
manager = StrategyManager()

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

            if config.BOT_MODE == "paper":
                if hasattr(config, 'PAPER_OPEN_POSITIONS'):
                    config.PAPER_OPEN_POSITIONS = []
                # ΔΙΟΡΘΩΣΗ: Καθαρίζουμε με ασφάλεια τα trailing stops από τον κεντρικό εγκέφαλο
                config.PAPER_TRAILING_STOPS = {}

    except Exception as e:
        logger.error(f"Σφάλμα κατά την αναγκαστική έξοδο: {e}")


# Προσθήκη global μεταβλητής για trailing stops (paper mode)
if not hasattr(config, 'PAPER_TRAILING_STOPS'):
    config.PAPER_TRAILING_STOPS = {}  # {symbol: {'entry_price': , 'highest_price': , 'stop_price': }}

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

                for symbol in pairs_to_check:
                    end_date = datetime.now(timezone.utc)
                    max_period = get_max_required_period()
                    timeframe_to_days = {
                        '1m': 1 / 1440, '5m': 5 / 1440, '15m': 15 / 1440, '30m': 30 / 1440,
                        '1h': 1 / 24, '4h': 4 / 24, '1d': 1, '1w': 7, '1M': 30
                    }
                    days_per_candle = timeframe_to_days.get(config.TIMEFRAME, 1)
                    lookback_days = max_period * days_per_candle * 2  # buffer 2x
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

                    # === BUY LOGIC ===
                    if (signal.action == 1 and
                            symbol not in open_positions and
                            BOT_STATE["is_buying_enabled"]):

                        # --- ΕΛΕΓΧΟΣ ΑΣΦΑΛΕΙΑΣ: Υπολογισμός αξίας θέσης σε USDC ---
                        trade_value = current_price * config.DEFAULT_TRADE_SIZE

                        # 1. Έλεγχος αν η θέση καλύπτει το ελάχιστο μέγεθος (MIN_POSITION_SIZE)
                        if trade_value < config.MIN_POSITION_SIZE:
                            logger.warning(
                                f"⚠️ [SAFETY] Παράκαμψη αγοράς για {symbol}. Η αξία θέσης (${trade_value:.2f}) είναι μικρότερη από το όριο MIN_POSITION_SIZE (${config.MIN_POSITION_SIZE:.2f})")
                            continue

                        # 2. Έλεγχος αν υπάρχει επαρκές υπόλοιπο (Μόνο για Paper Mode)
                        if config.BOT_MODE == "paper" and config.PAPER_BALANCE < trade_value:
                            logger.error(
                                f"❌ [BALANCE] Ανεπαρκές υπόλοιπο Paper Wallet για το {symbol}. Απαιτούνται: ${trade_value:.2f}, Διαθέσιμα: ${config.PAPER_BALANCE:.2f}")
                            continue

                        # Αν περάσουν οι έλεγχοι, εκτελείται η εντολή
                        res = exchange.place_order(symbol, "BUY", quantity=config.DEFAULT_TRADE_SIZE)

                        if config.BOT_MODE == "paper":
                            if not hasattr(config, 'PAPER_OPEN_POSITIONS'):
                                config.PAPER_OPEN_POSITIONS = []
                            config.PAPER_OPEN_POSITIONS.append(symbol)

                            # Αφαίρεση του ποσού από το Paper Wallet για ρεαλιστική προσομοίωση
                            config.PAPER_BALANCE -= trade_value

                            config.PAPER_TRAILING_STOPS[symbol] = {
                                "entry_price": current_price,
                                "highest_price": current_high,
                                "stop_price": current_price * (1 - config.STOP_LOSS_PCT)
                            }
                        logger.info(f"🟢 BUY {symbol} @ {current_price} (Αξία Θέσης: ${trade_value:.2f})")

                    # === SELL / TRAILING LOGIC ===
                    elif symbol in open_positions:
                        if config.BOT_MODE == "paper" and symbol in config.PAPER_TRAILING_STOPS:
                            pos = config.PAPER_TRAILING_STOPS[symbol]
                            pos["highest_price"] = max(pos["highest_price"], current_high)

                            trail_info = manager._update_trailing_stop(
                                df, pos["entry_price"], pos["highest_price"]
                            )

                            if trail_info["activated"]:
                                pos["stop_price"] = trail_info["stop_price"]

                            # Έλεγχος αν χτυπήθηκε Trailing Stop
                            if current_price <= pos["stop_price"]:
                                res = exchange.place_order(symbol, "SELL", quantity=config.DEFAULT_TRADE_SIZE)
                                config.PAPER_OPEN_POSITIONS.remove(symbol)
                                del config.PAPER_TRAILING_STOPS[symbol]

                                # Επιστροφή των δολαρίων στο Paper Wallet
                                sell_value = current_price * config.DEFAULT_TRADE_SIZE
                                config.PAPER_BALANCE += sell_value

                                logger.info(
                                    f"🔴 TRAILING STOP TRIGGERED {symbol} @ {current_price} (Επιστροφή Wallet: ${sell_value:.2f})")
                                continue

                        # Κανονικό Sell signal από δείκτες
                        elif signal.action == -1:
                            res = exchange.place_order(symbol, "SELL", quantity=config.DEFAULT_TRADE_SIZE)
                            if config.BOT_MODE == "paper" and symbol in config.PAPER_OPEN_POSITIONS:
                                config.PAPER_OPEN_POSITIONS.remove(symbol)
                                if symbol in config.PAPER_TRAILING_STOPS:
                                    del config.PAPER_TRAILING_STOPS[symbol]

                                # Επιστροφή των δολαρίων στο Paper Wallet
                                sell_value = current_price * config.DEFAULT_TRADE_SIZE
                                config.PAPER_BALANCE += sell_value

                                logger.info(
                                    f"🔴 SELL SIGNAL TRIGGERED {symbol} @ {current_price} (Επιστροφή Wallet: ${sell_value:.2f})")

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
        "base_currency": config.BASE_CURRENCY  # Προστέθηκε το base currency
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
    logger.debug("💾 [UI ACTION] Λήφθηκε αίτημα αποθήκευσης νέων ρυθμίσεων στρατηγικής.")

    # 1. ΒΑΣΙΚΕΣ ΡΥΘΜΙΣΕΙΣ (Timeframe, SL, TP, Logic)
    config.TIMEFRAME = request.form.get('timeframe', config.TIMEFRAME)
    manager.strategy_cfg['COMBINATION_LOGIC'] = request.form.get('logic',
                                                                 manager.strategy_cfg.get('COMBINATION_LOGIC', 'AND'))

    try:
        # Τα defaults έρχονται δυναμικά από το κεντρικό config
        config.STOP_LOSS_PCT = float(request.form.get('sl_pct', config.STOP_LOSS_PCT * 100)) / 100
        config.TAKE_PROFIT_PCT = float(request.form.get('tp_pct', config.TAKE_PROFIT_PCT * 100)) / 100
        logger.debug(
            f"📈 [STRATEGY UPDATE] Νέο SL: {config.STOP_LOSS_PCT * 100}% | Νέο TP: {config.TAKE_PROFIT_PCT * 100}% | Timeframe: {config.TIMEFRAME}")
    except Exception as e:
        logger.error(f"❌ Σφάλμα κατά τη μετατροπή SL/TP: {e}")

    # 2. ΡΥΘΜΙΣΕΙΣ ΔΕΙΚΤΩΝ (Indicators)
    for ind_name, ind_params in manager.strategy_cfg.items():
        if not isinstance(ind_params, dict): continue

        # Ενεργοποίηση / Απενεργοποίηση Δείκτη
        checkbox_val = request.form.get(f"use_{ind_name}")
        old_status = ind_params.get("enabled", False)
        ind_params["enabled"] = (checkbox_val == 'true')

        if old_status != ind_params["enabled"]:
            logger.debug(
                f"🔔 [INDICATOR TOGGLE] Ο δείκτης {ind_name} άλλαξε κατάσταση σε: {'ΕΝΕΡΓΟΣ' if ind_params['enabled'] else 'ΑΠΕΝΕΡΓΟΣ'}")

        # Αλλαγή εσωτερικών παραμέτρων (π.χ. periods, oversold/overbought)
        for param_name, old_val in ind_params.items():
            if param_name in ['enabled', 'signal_options']: continue

            form_val = request.form.get(f"{ind_name}_{param_name}")
            if form_val is not None:
                try:
                    # Μετατροπή στον σωστό τύπο δεδομένων (int, float ή string)
                    if isinstance(old_val, int):
                        manager.strategy_cfg[ind_name][param_name] = int(form_val)
                    elif isinstance(old_val, float):
                        manager.strategy_cfg[ind_name][param_name] = float(form_val)
                    else:
                        manager.strategy_cfg[ind_name][param_name] = str(form_val)
                except ValueError:
                    logger.warning(f"⚠️ Αποτυχία μετατροπής τύπου για {ind_name}_{param_name} με τιμή '{form_val}'.")
                    pass

    # Ανανέωση της λίστας των ενεργών δεικτών στον manager
    manager.active_indicators = [name for name, cfg in manager.strategy_cfg.items() if
                                 isinstance(cfg, dict) and cfg.get("enabled", False)]
    logger.info(f"🎯 [STRATEGY SYNC] Ενεργοί Δείκτες Συστήματος: {manager.active_indicators}")

    # 3. TRAILING STOP SETTINGS
    config.TRAILING_STOP_CONFIG["enabled"] = (request.form.get('use_trailing_stop') == 'true')
    config.TRAILING_STOP_CONFIG["method"] = request.form.get('trailing_method',
                                                             config.TRAILING_STOP_CONFIG.get("method", "percent"))
    config.TRAILING_STOP_CONFIG["use_alongside_fixed_sl"] = (request.form.get('use_fixed_sl_with_trailing') == 'true')

    try:
        # 1. Κρατάμε τις τρέχουσες τιμές για fallback (υπάρχει ήδη στον κώδικά σου)
        current_trail = config.TRAILING_STOP_CONFIG.get("trail_pct", 0.03)
        current_activation = config.TRAILING_STOP_CONFIG.get("activation_pct", 0.01)

        # 2. Αν ο χρήστης έστειλε κάτι από τη φόρμα, το μετατρέπουμε. (Υπάρχει ήδη)
        form_trail = request.form.get('trail_pct')
        form_activation = request.form.get('activation_pct')

        config.TRAILING_STOP_CONFIG["trail_pct"] = float(form_trail) / 100 if form_trail else current_trail
        config.TRAILING_STOP_CONFIG["activation_pct"] = float(
            form_activation) / 100 if form_activation else current_activation

        # =================================================================
        # 📈 ΕΔΩ ΒΑΖΕΙΣ ΤΗΝ ΠΡΟΣΘΗΚΗ ΓΙΑ ΤΑ ΥΠΟΛΟΙΠΑ ΤΡΑILING FIELDS:
        # =================================================================
        # Ανάκτηση των νέων πεδίων από τη φόρμα (request.form)
        form_enabled = request.form.get('use_trailing_stop')  # Checkbox (επιστρέφει 'on' αν είναι επιλεγμένο)
        form_method = request.form.get('trailing_stop_method')  # Dropdown ('percent' ή 'atr')
        form_atr_multiplier = request.form.get('atr_multiplier')  # Number input
        form_alongside_sl = request.form.get('use_alongside_fixed_sl')  # Checkbox

        # Ενημέρωση του config dict με σωστό type casting
        config.TRAILING_STOP_CONFIG["enabled"] = True if form_enabled in ['on', 'true', True] else False

        if form_method:
            config.TRAILING_STOP_CONFIG["method"] = form_method

        if form_atr_multiplier:
            config.TRAILING_STOP_CONFIG["atr_multiplier"] = float(form_atr_multiplier)

        config.TRAILING_STOP_CONFIG["use_alongside_fixed_sl"] = True if form_alongside_sl in ['on', 'true',
                                                                                              True] else False
        # =================================================================

        logger.debug(
            f"↩️ [TRAILING UPDATE] Trail: {config.TRAILING_STOP_CONFIG['trail_pct'] * 100}% | Activation: {config.TRAILING_STOP_CONFIG['activation_pct'] * 100}%")
    except Exception as e:
        logger.error(f"❌ Σφάλμα κατά τη μετατροπή Trailing parameters: {e}")

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

if __name__ == '__main__':
    app.run(debug=True, use_reloader=False)