# =============================================================================
# position_manager.py
# =============================================================================
# Διαχειρίζεται το ΑΝΟΙΓΜΑ και το ΚΛΕΙΣΙΜΟ θέσεων (Paper & Live) για ΕΝΑ
# symbol, μέσα στο auto_trade_loop() του app.py.
#
# Σκοπός: Το app.py μένει "λεπτό" (μόνο scheduling/orchestration), και όλα
# τα κατώφλια (Stop Loss %, Take Profit %, Trailing Stop config, Min
# Position Size, Default Trade Size κλπ.) έρχονται ΑΠΟΚΛΕΙΣΤΙΚΑ από το
# config.py (κεντρικός εγκέφαλος) - τίποτα hardcoded εδώ.
#
# Σειρά ελέγχων κλεισίματος θέσης (try_close_position):
#   1. TAKE PROFIT  -> current_price >= entry_price * (1 + config.TAKE_PROFIT_PCT)
#   2. TRAILING / FIXED STOP -> current_price <= pos["stop_price"]
#   3. INDICATOR SELL SIGNAL -> signal.action == -1
#
# ΣΗΜΕΙΩΣΗ (fix bug #3): Παλιότερα το Take Profit (config.TAKE_PROFIT_PCT)
# υπολογιζόταν στο Signal αλλά ΔΕΝ ελεγχόταν ΠΟΥΘΕΝΑ στο live/paper loop -
# οι θέσεις έκλειναν μόνο μέσω trailing/fixed stop ή indicator signal.
# Επίσης, το indicator SELL signal ελέγχονταν ΜΟΝΟ όταν ΔΕΝ υπήρχε
# καταγεγραμμένο trailing-stop record - δηλαδή στο Paper Mode (που έχει
# πάντα record) ο SELL signal έλεγχος ήταν ΑΣΤΕ ΠΟΤΕ ΕΝΕΡΓΟΣ. Τώρα και οι
# 3 έλεγχοι είναι ανεξάρτητοι και τρέχουν με τη σειρά πάνω.
#
# ΣΗΜΕΙΩΣΗ (fix #6 - Live Mode χωρίς SL/TP/Trailing): Παλιότερα το
# config.PAPER_TRAILING_STOPS (entry_price/highest_price/stop_price) γέμιζε
# ΜΟΝΟ σε Paper Mode -> σε Live Mode ΔΕΝ υπήρχε ΚΑΝΕΝΑΣ έλεγχος TP/SL/
# Trailing, μόνο indicator sell signal. Το dict μετονομάστηκε σε
# config.OPEN_POSITIONS_DATA και γεμίζει ΠΑΝΤΑ (Paper ΚΑΙ Live) όταν το ίδιο
# το bot ανοίγει θέση, ώστε οι έλεγχοι 1 & 2 να δουλεύουν σε ΚΑΙ ΤΑ ΔΥΟ MODE.
# Το PAPER_BALANCE / PAPER_OPEN_POSITIONS bookkeeping παραμένει ΜΟΝΟ για
# Paper Mode (config = κεντρικός εγκέφαλος για το BOT_MODE).
# =============================================================================

import config
from utils.logger import logger


def try_open_position(exchange, symbol, signal, current_price, current_high, open_positions, bot_state) -> bool:
    """
    Ελέγχει τις προϋποθέσεις και ανοίγει νέα θέση (BUY) αν:
      - Το σήμα είναι BUY (signal.action == 1)
      - Δεν υπάρχει ήδη ανοιχτή θέση στο symbol
      - Το bot επιτρέπει νέες αγορές (bot_state["is_buying_enabled"])
      - Η αξία θέσης >= config.MIN_POSITION_SIZE
      - (Paper mode) Υπάρχει αρκετό υπόλοιπο στο config.PAPER_BALANCE

    Επιστρέφει True αν ανοίχτηκε θέση, αλλιώς False.
    """
    if not (signal.action == 1 and symbol not in open_positions and bot_state["is_buying_enabled"]):
        return False

    # --- ΕΛΕΓΧΟΣ ΑΣΦΑΛΕΙΑΣ: Υπολογισμός αξίας θέσης σε Base Currency ---
    trade_value = current_price * config.DEFAULT_TRADE_SIZE

    # 1. Η θέση πρέπει να καλύπτει το ελάχιστο μέγεθος (MIN_POSITION_SIZE)
    if trade_value < config.MIN_POSITION_SIZE:
        logger.warning(
            f"⚠️ [SAFETY] Παράκαμψη αγοράς για {symbol}. Η αξία θέσης (${trade_value:.2f}) "
            f"είναι μικρότερη από το όριο MIN_POSITION_SIZE (${config.MIN_POSITION_SIZE:.2f})")
        return False

    # 2. (Μόνο Paper Mode) Πρέπει να υπάρχει αρκετό εικονικό υπόλοιπο
    if config.BOT_MODE == "paper" and config.PAPER_BALANCE < trade_value:
        logger.error(
            f"❌ [BALANCE] Ανεπαρκές υπόλοιπο Paper Wallet για το {symbol}. "
            f"Απαιτούνται: ${trade_value:.2f}, Διαθέσιμα: ${config.PAPER_BALANCE:.2f}")
        return False

    # Αν περάσουν οι έλεγχοι, εκτελείται η εντολή
    exchange.place_order(symbol, "BUY", quantity=config.DEFAULT_TRADE_SIZE)

    if config.BOT_MODE == "paper":
        if not hasattr(config, 'PAPER_OPEN_POSITIONS'):
            config.PAPER_OPEN_POSITIONS = []
        config.PAPER_OPEN_POSITIONS.append(symbol)

        # Αφαίρεση του ποσού από το Paper Wallet για ρεαλιστική προσομοίωση
        config.PAPER_BALANCE -= trade_value

    # Καταγραφή entry/highest/stop_price -> χρησιμοποιείται από το
    # try_close_position() για Take Profit, Trailing & Fixed Stop Loss.
    # (fix #6: τρέχει ΠΑΝΤΑ, σε Paper ΚΑΙ σε Live Mode, ώστε να υπάρχει
    # record και για τις θέσεις που ανοίγει το bot σε Live Mode)
    config.OPEN_POSITIONS_DATA[symbol] = {
        "entry_price": current_price,
        "highest_price": current_high,
        "stop_price": current_price * (1 - config.STOP_LOSS_PCT)
    }

    logger.info(f"🟢 BUY {symbol} @ {current_price} (Αξία Θέσης: ${trade_value:.2f})")
    return True


def _execute_sell(exchange, symbol, current_price, exit_reason: str) -> None:
    """
    Στέλνει την εντολή SELL στο exchange και κάνει το "καθάρισμα" της θέσης
    από το config.OPEN_POSITIONS_DATA (Paper ΚΑΙ Live Mode). Σε Paper Mode
    επιπλέον αφαιρεί το symbol από το PAPER_OPEN_POSITIONS και επιστρέφει
    τα δολάρια στο Paper Wallet.
    """
    exchange.place_order(symbol, "SELL", quantity=config.DEFAULT_TRADE_SIZE)

    # Καθάρισμα του record παρακολούθησης -> τρέχει ΠΑΝΤΑ (fix #6)
    if symbol in config.OPEN_POSITIONS_DATA:
        del config.OPEN_POSITIONS_DATA[symbol]

    if config.BOT_MODE == "paper":
        if symbol in config.PAPER_OPEN_POSITIONS:
            config.PAPER_OPEN_POSITIONS.remove(symbol)

        # Επιστροφή των δολαρίων στο Paper Wallet
        sell_value = current_price * config.DEFAULT_TRADE_SIZE
        config.PAPER_BALANCE += sell_value

        logger.info(f"🔴 {exit_reason} {symbol} @ {current_price} (Επιστροφή Wallet: ${sell_value:.2f})")
    else:
        logger.info(f"🔴 {exit_reason} {symbol} @ {current_price}")


def try_close_position(exchange, manager, symbol, df, signal, current_price, current_high) -> bool:
    """
    Ελέγχει αν πρέπει να κλείσει μια ΑΝΟΙΧΤΗ θέση και, αν ναι, την κλείνει.

    Έλεγχοι (με αυτή τη σειρά, ο πρώτος που "χτυπά" κλείνει τη θέση):
      1. TAKE PROFIT  : current_price >= entry_price * (1 + config.TAKE_PROFIT_PCT)
      2. TRAILING / FIXED STOP : current_price <= pos["stop_price"]
         (το pos["stop_price"] ξεκινάει ως fixed SL = entry*(1-SL%) και
          ενημερώνεται από τον StrategyManager._update_trailing_stop όταν
          ενεργοποιηθεί το trailing, σύμφωνα με config.TRAILING_STOP_CONFIG)
      3. INDICATOR SELL SIGNAL : signal.action == -1

    Οι έλεγχοι 1 & 2 χρειάζονται καταγεγραμμένο record στο
    config.OPEN_POSITIONS_DATA. Αυτό το record δημιουργείται από το
    try_open_position() σε ΚΑΘΕ mode (fix #6), οπότε οι έλεγχοι 1 & 2
    τρέχουν πλέον και σε Paper ΚΑΙ σε Live Mode.

    ΠΡΟΣΟΧΗ: Θέσεις που υπήρχαν ΠΡΙΝ ξεκινήσει το bot (ή ανοίχτηκαν χειροκίνητα
    σε Live Mode) δεν έχουν record στο OPEN_POSITIONS_DATA -> γι' αυτές
    ελέγχεται μόνο το (3) indicator sell signal, όπως και πριν.

    Επιστρέφει True αν η θέση έκλεισε, αλλιώς False.
    """
    has_position_record = (symbol in config.OPEN_POSITIONS_DATA)

    if has_position_record:
        pos = config.OPEN_POSITIONS_DATA[symbol]
        pos["highest_price"] = max(pos["highest_price"], current_high)

        # --- 1. TAKE PROFIT ---
        take_profit_price = pos["entry_price"] * (1 + config.TAKE_PROFIT_PCT)
        if current_price >= take_profit_price:
            _execute_sell(exchange, symbol, current_price, "TAKE PROFIT TRIGGERED")
            return True

        # --- 2. TRAILING / FIXED STOP ---
        trail_info = manager._update_trailing_stop(df, pos["entry_price"], pos["highest_price"])
        if trail_info["activated"]:
            pos["stop_price"] = trail_info["stop_price"]

        if current_price <= pos["stop_price"]:
            _execute_sell(exchange, symbol, current_price, "TRAILING STOP TRIGGERED")
            return True

    # --- 3. INDICATOR SELL SIGNAL ---
    if signal.action == -1:
        _execute_sell(exchange, symbol, current_price, "SELL SIGNAL TRIGGERED")
        return True

    return False
