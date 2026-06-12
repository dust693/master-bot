# =============================================================================
# settings_updater.py
# =============================================================================
# Συγκεντρώνει τη λογική που ενημερώνει το config.py (και τον StrategyManager)
# όταν ο χρήστης πατάει "Αποθήκευση" στο UI (route /update_strategy στο
# app.py). Σκοπός:
#   - Το app.py μένει καθαρό (μόνο routing / orchestration).
#   - Όλα τα "fallback" / default values αντλούνται ΠΑΝΤΑ από τις ΤΡΕΧΟΥΣΕΣ
#     τιμές του config.py (config = κεντρικός εγκέφαλος) - όχι hardcoded
#     νούμερα μέσα σε αυτό το αρχείο.
# =============================================================================

import config
from utils.logger import logger


def _parse_percent(form_value, fallback: float) -> float:
    """
    Μετατρέπει μια τιμή ποσοστού από τη φόρμα (π.χ. "3" για 3%) σε δεκαδικό
    (0.03), όπως αποθηκεύεται στο config.

    Αν η φόρμα δεν έστειλε τιμή ή η τιμή δεν είναι έγκυρος αριθμός,
    επιστρέφεται το 'fallback' (συνήθως η ΤΡΕΧΟΥΣΑ τιμή από το config),
    ώστε καμία ρύθμιση να μη "χαθεί" / μηδενιστεί κατά λάθος.
    """
    if form_value in (None, ""):
        return fallback
    try:
        return float(form_value) / 100
    except ValueError:
        logger.warning(f"⚠️ Μη έγκυρη τιμή ποσοστού: '{form_value}'. Διατηρείται η προηγούμενη τιμή ({fallback*100}%).")
        return fallback


def apply_core_settings(form) -> None:
    """
    Ενημερώνει τις βασικές κεντρικές ρυθμίσεις: Timeframe, Stop Loss %,
    Take Profit %. Τα fallback values είναι ΠΑΝΤΑ οι τρέχουσες τιμές του
    config (δηλ. αν ο χρήστης δεν αλλάξει κάτι, παραμένει ως είχε).
    """
    config.TIMEFRAME = form.get('timeframe', config.TIMEFRAME)

    config.STOP_LOSS_PCT = _parse_percent(form.get('sl_pct'), config.STOP_LOSS_PCT)
    config.TAKE_PROFIT_PCT = _parse_percent(form.get('tp_pct'), config.TAKE_PROFIT_PCT)

    logger.debug(
        f"📈 [STRATEGY UPDATE] Νέο SL: {config.STOP_LOSS_PCT * 100}% | "
        f"Νέο TP: {config.TAKE_PROFIT_PCT * 100}% | Timeframe: {config.TIMEFRAME}")


def apply_indicator_settings(form, manager) -> None:
    """
    Ενημερώνει το manager.strategy_cfg (= config.STRATEGY_CONFIG) με βάση
    τη φόρμα:
      - COMBINATION_LOGIC (AND / OR / MAJORITY)
      - enabled/disabled για κάθε δείκτη (checkbox "use_<ΔΕΙΚΤΗΣ>")
      - επιμέρους παραμέτρους κάθε δείκτη (π.χ. "<ΔΕΙΚΤΗΣ>_period"),
        με αυτόματη μετατροπή στον τύπο που ήδη έχει η default τιμή
        (int / float / string) - ώστε το config να ορίζει τον "σωστό τύπο".
    Στο τέλος ξαναϋπολογίζει το manager.active_indicators.
    """
    manager.strategy_cfg['COMBINATION_LOGIC'] = form.get(
        'logic', manager.strategy_cfg.get('COMBINATION_LOGIC', 'AND'))

    for ind_name, ind_params in manager.strategy_cfg.items():
        if not isinstance(ind_params, dict):
            continue  # Το 'COMBINATION_LOGIC' είναι string, όχι dict -> skip

        # --- Ενεργοποίηση / Απενεργοποίηση Δείκτη ---
        checkbox_val = form.get(f"use_{ind_name}")
        old_status = ind_params.get("enabled", False)
        ind_params["enabled"] = (checkbox_val == 'true')

        if old_status != ind_params["enabled"]:
            logger.debug(
                f"🔔 [INDICATOR TOGGLE] Ο δείκτης {ind_name} άλλαξε κατάσταση σε: "
                f"{'ΕΝΕΡΓΟΣ' if ind_params['enabled'] else 'ΑΠΕΝΕΡΓΟΣ'}")

        # --- Επιμέρους παράμετροι (periods, oversold/overbought, κλπ.) ---
        for param_name, old_val in ind_params.items():
            if param_name in ['enabled', 'signal_options']:
                continue

            form_val = form.get(f"{ind_name}_{param_name}")
            if form_val is None:
                continue  # Η φόρμα δεν έστειλε αυτό το πεδίο -> κρατάμε την παλιά τιμή

            try:
                # Η τρέχουσα τιμή στο config καθορίζει τον τύπο μετατροπής
                if isinstance(old_val, int):
                    ind_params[param_name] = int(form_val)
                elif isinstance(old_val, float):
                    ind_params[param_name] = float(form_val)
                else:
                    ind_params[param_name] = str(form_val)
            except ValueError:
                logger.warning(f"⚠️ Αποτυχία μετατροπής τύπου για {ind_name}_{param_name} με τιμή '{form_val}'.")

    # Ανανέωση της λίστας ενεργών δεικτών στον manager
    manager.active_indicators = [
        name for name, cfg in manager.strategy_cfg.items()
        if isinstance(cfg, dict) and cfg.get("enabled", False)
    ]
    logger.info(f"🎯 [STRATEGY SYNC] Ενεργοί Δείκτες Συστήματος: {manager.active_indicators}")


def apply_trailing_stop_settings(form) -> None:
    """
    Ενημερώνει το config.TRAILING_STOP_CONFIG με βάση τη φόρμα.

    Πεδία φόρμας (βλ. templates/partials/tab_control.html):
      - "use_trailing_stop"        (checkbox, value="true")
      - "trailing_method"          (dropdown: "percent" ή "atr")
      - "trail_pct"                (number, %)
      - "activation_pct"           (number, %)
      - "use_fixed_sl_with_trailing" (checkbox, value="true")

    ΔΙΟΡΘΩΣΗ BUG: Παλιότερα υπήρχε ένα ΔΕΥΤΕΡΟ (διπλό) block μετά από αυτό
    που ξαναδιάβαζε τα ίδια πεδία αλλά με ΛΑΘΟΣ ονόματα που ΔΕΝ υπάρχουν
    στη φόρμα (π.χ. "use_alongside_fixed_sl" αντί για το σωστό
    "use_fixed_sl_with_trailing", "trailing_stop_method" αντί για
    "trailing_method"). Αυτό έκανε το form.get() να επιστρέφει πάντα None,
    με αποτέλεσμα η ρύθμιση "Μαζί με Fixed SL" (use_alongside_fixed_sl)
    να ΜΗΔΕΝΙΖΕΤΑΙ ΠΑΝΤΑ σε False, ανεξάρτητα από την επιλογή του χρήστη.
    Το διπλό block αφαιρέθηκε - η λογική υπάρχει πλέον ΜΙΑ φορά, σωστά.
    """
    ts_cfg = config.TRAILING_STOP_CONFIG

    # --- Checkboxes ---
    # Το HTML στέλνει value="true" όταν είναι επιλεγμένα, και ΔΕΝ στέλνει
    # καθόλου το key όταν ΔΕΝ είναι επιλεγμένα (form.get -> None).
    ts_cfg["enabled"] = (form.get('use_trailing_stop') == 'true')
    ts_cfg["use_alongside_fixed_sl"] = (form.get('use_fixed_sl_with_trailing') == 'true')

    # --- Dropdown method ("percent" ή "atr") ---
    # Αν δεν σταλεί (δεν θα συμβεί αφού είναι <select>), κρατάμε την τρέχουσα τιμή του config.
    ts_cfg["method"] = form.get('trailing_method', ts_cfg.get("method", "percent"))

    # --- Ποσοστά (%) -> δεκαδικά, με fallback στις ΤΡΕΧΟΥΣΕΣ τιμές του config ---
    ts_cfg["trail_pct"] = _parse_percent(form.get('trail_pct'), ts_cfg.get("trail_pct", 0.03))
    ts_cfg["activation_pct"] = _parse_percent(form.get('activation_pct'), ts_cfg.get("activation_pct", 0.01))

    # --- ATR multiplier (προαιρετικό πεδίο, δεν υπάρχει ακόμα στο UI) ---
    form_atr_multiplier = form.get('atr_multiplier')
    if form_atr_multiplier:
        try:
            ts_cfg["atr_multiplier"] = float(form_atr_multiplier)
        except ValueError:
            logger.warning(f"⚠️ Μη έγκυρη τιμή ATR multiplier: '{form_atr_multiplier}'.")

    logger.debug(
        f"↩️ [TRAILING UPDATE] Enabled: {ts_cfg['enabled']} | Method: {ts_cfg['method']} | "
        f"Trail: {ts_cfg['trail_pct'] * 100}% | Activation: {ts_cfg['activation_pct'] * 100}% | "
        f"Μαζί με Fixed SL: {ts_cfg['use_alongside_fixed_sl']}")
