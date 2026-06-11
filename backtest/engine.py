import config
from datetime import datetime, timedelta
from indicators.technical_analysis import calculate_all_indicators
import pandas as pd
import pandas_ta as ta
from backtest.metrics import calculate_advanced_metrics
from utils.logger import *
from backtest.trend_cache import TrendCache

def run_backtester(form_data, exchange, manager):
    # Μηδενισμός του διακόπτη ακύρωσης κατά την εκκίνηση
    config.BACKTEST_CONFIG["cancel"] = False

    try:
        if not manager.active_indicators:
            return {"error": "Πρέπει πρώτα να επιλέξεις και να ενεργοποιήσεις δείκτες!"}

        # Ο επιθυμητός αριθμός ΚΕΡΔΟΦΟΡΩΝ ζευγών που θέλει ο χρήστης
        target_profitable_pairs = int(form_data.get('pair_count', 5))
        
        start_date = form_data.get('start_date')
        end_date = form_data.get('end_date')

        # --- Ζητάμε ΑΜΕΣΑ μια μεγάλη λίστα από το API βάσει όγκου 24h λαμβάνοντας υπόψη το Trading Type ---
        pool_size = config.SCANNER_PAIR_LIMIT

        # Διαβάζουμε δυναμικά αν το UI έχει επιλέξει spot ή margin
        trading_type = getattr(config, "TRADING_TYPE", "spot")

        # Καλούμε τη συνάρτηση περνώντας και το trading_type
        top_pairs_pool = exchange.get_top_volume_pairs(
            limit=pool_size,
            quote_asset=config.BASE_CURRENCY,
            trading_type=trading_type
        )
        
        if not top_pairs_pool: 
            return {"error": "Δεν βρέθηκαν δεδομένα αγοράς."}

        # Φιλτράρισμα με βάση τη Blacklist ΠΡΙΝ ξεκινήσουμε το κατέβασμα ιστορικού
        blacklist = config.SYMBOL_FILTERS.get("blacklist", []) if hasattr(config, 'SYMBOL_FILTERS') else []
        pairs_to_check = [p for p in top_pairs_pool if p not in blacklist]

        # =========================================================================
        # 👑 GLOBAL MARKET FILTER: BITCOIN UPTREND PREPARATION (Μια φορά στην αρχή)
        # =========================================================================
        trend_cache = TrendCache(exchange)
        btc_filter_dict = trend_cache.get_btc_trend_filter(start_date, end_date)

        # Μετατροπή του dictionary σε DataFrame
        btc_trend_df = pd.DataFrame(list(btc_filter_dict.items()), columns=['timestamp', 'is_btc_bullish'])

        # Εξασφαλίζουμε σωστό datetime format χωρίς timezones για απόλυτη συμβατότητα στο merge
        btc_trend_df['timestamp'] = pd.to_datetime(btc_trend_df['timestamp']).dt.tz_localize(None)
        btc_trend_df = btc_trend_df.sort_values('timestamp')

        print(f"✅ Φίλτρο BTC έτοιμο (χρήση 1d & 4h trend cache).")

        # =========================================================================
        # 🔬 BACKTEST LOOP ΓΙΑ ΤΑ ALTCOINS
        # =========================================================================
        successful_pairs_history = []
        pairs_summary = []
        pair_details = {}
        profitable_pairs_found = 0
        
        overall_initial = config.PAPER_BALANCE
        overall_balance = overall_initial
        total_trades = 0
        checked_pairs = 0

        for symbol in pairs_to_check:

            # ΕΛΕΓΧΟΣ ΑΚΥΡΩΣΗΣ: Αν ο χρήστης πάτησε Cancel, σταμάτα αμέσως!
            # Χρησιμοποιούμε .get() για ασφάλεια, ώστε να μην κρασάρει αν λείπει το key
            if config.BACKTEST_CONFIG.get('cancel', False):
                logger.warning("🛑 Το Backtest ακυρώθηκε από το χρήστη.")
                break
                
            if profitable_pairs_found >= target_profitable_pairs:
                break
                
            df = exchange.get_candles_cached(symbol, config.TIMEFRAME, start_date, end_date)
            if df is None or df.empty:
                continue

            checked_pairs += 1
                
            # Υπολογισμός τεχνικών δεικτών & σημάτων (signal: +1 = Αγορά, -1 = Έξοδος)
            df = calculate_all_indicators(df, manager.strategy_cfg)

            # Μετατρέπουμε την κατάσταση θέσης σε string: None, 'long', 'short'
            position_type = None
            entry_price = 0.0
            entry_time = None
            position_extreme = 0.0  # Θα κρατάει το High για Longs και το Low για Shorts (για το Trailing)
            trades = []

            # --- ΕΝΣΩΜΑΤΩΣΗ ΤΟΥ ΦΙΛΤΡΟΥ BTC ΣΤΟ DF ΤΟΥ ΣΥΓΚΕΚΡΙΜΕΝΟΥ ΝΟΜΙΣΜΑΤΟΣ ---
            # Μετρέπουμε το timestamp του νομίσματος σε datetime (tz-naive) για να ταιριάζει με το BTC
            df['timestamp'] = pd.to_datetime(df['timestamp']).dt.tz_localize(None)
            df = df.sort_values('timestamp')

            # merge_asof: Κουμπώνει αυτόματα σε κάθε κερί το status του BTC εκείνης της στιγμής
            df = pd.merge_asof(df, btc_trend_df, on='timestamp', direction='backward')

            # Γέμισμα τυχόν κενών στην αρχή με True
            df['is_btc_bullish'] = df['is_btc_bullish'].fillna(True)

            # =========================================================================
            # 🔄 Προσομοίωση candle-by-candle
            # =========================================================================
            for idx, row in df.iterrows():
                current_time = row['timestamp']
                current_close = float(row['close'])
                current_high = float(row['high'])
                current_low = float(row['low'])

                is_btc_bullish = bool(row['is_btc_bullish'])
                trading_type = getattr(config, 'TRADING_TYPE', 'spot')

                # =============================================================
                # Α) ΔΙΑΧΕΙΡΙΣΗ ΑΝΟΙΧΤΗΣ ΘΕΣΗΣ
                # =============================================================
                if position_type is not None:
                    exit_trade = False
                    exit_reason = ""
                    exit_price = current_close
                    stop_loss_pct = getattr(config, 'STOP_LOSS_PCT', 0.05)
                    take_profit_pct = getattr(config, 'TAKE_PROFIT_PCT', 0.10)
                    ts_config = getattr(config, 'TRAILING_STOP_CONFIG', {})

                    # --- 1. ΔΙΑΧΕΙΡΙΣΗ LONG ΘΕΣΗΣ ---
                    if position_type == 'long':
                        if current_high > position_extreme: position_extreme = current_high  # Update peak

                        # Fixed SL / TP
                        sl_price = entry_price * (1 - stop_loss_pct)
                        tp_price = entry_price * (1 + take_profit_pct)

                        if current_low <= sl_price:
                            exit_trade, exit_reason, exit_price = True, "Stop Loss (Long)", sl_price
                        elif current_high >= tp_price:
                            exit_trade, exit_reason, exit_price = True, "Take Profit (Long)", tp_price

                        # Trailing Stop Long
                        elif ts_config.get('enabled', False) and (
                                (position_extreme - entry_price) / entry_price >= ts_config.get('activation_pct',
                                                                                                0.01)):
                            ts_price = position_extreme * (1 - ts_config.get('trail_pct', 0.03))
                            if current_low <= ts_price:
                                exit_trade, exit_reason, exit_price = True, "Trailing Stop (Long)", max(ts_price,
                                                                                                        current_close)

                        # Signal Exit (Short Signal -1 closes Long)
                        elif row.get('signal') == -1:
                            exit_trade, exit_reason, exit_price = True, "Indicator Exit Signal (Long)", current_close

                    # --- 2. ΔΙΑΧΕΙΡΙΣΗ SHORT ΘΕΣΗΣ (ΜΟΝΟ ΑΝ ΕΙΝΑΙ MARGIN) ---
                    elif position_type == 'short':
                        if current_low < position_extreme: position_extreme = current_low  # Update trough

                        # Στα Shorts το SL είναι ΠΑΝΩ και το TP είναι ΚΑΤΩ
                        sl_price = entry_price * (1 + stop_loss_pct)
                        tp_price = entry_price * (1 - take_profit_pct)

                        if current_high >= sl_price:
                            exit_trade, exit_reason, exit_price = True, "Stop Loss (Short)", sl_price
                        elif current_low <= tp_price:
                            exit_trade, exit_reason, exit_price = True, "Take Profit (Short)", tp_price

                        # Trailing Stop Short
                        elif ts_config.get('enabled', False) and (
                                (entry_price - position_extreme) / entry_price >= ts_config.get('activation_pct',
                                                                                                0.01)):
                            ts_price = position_extreme * (1 + ts_config.get('trail_pct', 0.03))
                            if current_high >= ts_price:
                                exit_trade, exit_reason, exit_price = True, "Trailing Stop (Short)", min(ts_price,
                                                                                                         current_close)

                        # Signal Exit (Long Signal +1 closes Short)
                        elif row.get('signal') == 1:
                            exit_trade, exit_reason, exit_price = True, "Indicator Exit Signal (Short)", current_close

                    # --- ΕΚΤΕΛΕΣΗ ΕΞΟΔΟΥ (ΚΟΙΝΗ) ---
                    if exit_trade:
                        # Υπολογισμός κέρδους (Στα shorts το κέρδος βγαίνει αν η τιμή πέσει)
                        if position_type == 'long':
                            pnl_pct = ((exit_price - entry_price) / entry_price) * 100
                        else:
                            pnl_pct = ((entry_price - exit_price) / entry_price) * 100

                        trades.append({
                            "symbol": symbol, "entry_time": entry_time, "entry_price": entry_price,
                            "exit_time": current_time, "exit_price": exit_price, "pnl_pct": pnl_pct,
                            "reason": exit_reason
                        })
                        position_type = None  # Επιστροφή σε flat κατάσταση

                # =============================================================
                # Β) ΕΛΕΓΧΟΣ ΓΙΑ ΑΝΟΙΓΜΑ ΝΕΑΣ ΘΕΣΗΣ (ΟΤΑΝ ΕΙΜΑΣΤΕ OUT)
                # =============================================================
                else:
                    # Σήμα LONG (+1): Ανοίγει πάντα αν το BTC είναι bullish
                    if row.get('signal') == 1 and is_btc_bullish:
                        position_type = 'long'
                        entry_price, entry_time = current_close, current_time
                        position_extreme = current_high

                    # Σήμα SHORT (-1): Ανοίγει ΜΟΝΟ αν έχουμε επιλέξει MARGIN στο UI
                    elif row.get('signal') == -1 and trading_type == 'margin' and not is_btc_bullish:
                        position_type = 'short'
                        entry_price, entry_time = current_close, current_time
                        position_extreme = current_low

                # Force close στο τέλος του backtest
            if position_type is not None:
                last_row = df.iloc[-1]
                exit_price = float(last_row['close'])
                if position_type == 'long':
                    pnl_pct = ((exit_price - entry_price) / entry_price) * 100
                else:
                    pnl_pct = ((entry_price - exit_price) / entry_price) * 100
                trades.append({
                    "symbol": symbol, "entry_time": entry_time, "entry_price": entry_price,
                    "exit_time": last_row['timestamp'], "exit_price": exit_price, "pnl_pct": pnl_pct,
                    "reason": f"Force Close (End of Backtest {position_type.upper()})"
                })
                
            # Ανάλυση και αποθήκευση αποτελεσμάτων μόνο αν το ζεύγος βγήκε κερδοφόρο
            if trades:
                pair_total_pnl_pct = sum([t['pnl_pct'] for t in trades])
                
                if pair_total_pnl_pct > 0:
                    profitable_pairs_found += 1
                    successful_pairs_history.extend(trades)
                    total_trades += len(trades)
                    
                    pairs_summary.append({
                        "symbol": symbol,
                        "profit_pct": round(pair_total_pnl_pct, 2),
                        "trades_count": len(trades)
                    })
                    
                    # Διαχωρισμός δεικτών για την εμφάνιση στο Plotly (UI)
                    overlays = []
                    oscillators = []
                    for col in df.columns:
                        if col in ['open', 'high', 'low', 'close', 'volume', 'timestamp', 'signal'] or 'signal' in col:
                            continue
                        if any(x in col for x in ['SMA', 'EMA', 'DEMA', 'TEMA', 'WMA', 'VWAP', 'BB', 'KC', 'DC']):
                            overlays.append(col)
                        else:
                            oscillators.append(col)
                            
                    pair_details[symbol] = {
                        "candles": df.to_dict(orient="records"),
                        "indicators": {
                            "overlays": overlays,
                            "oscillators": oscillators
                        }
                    }
                    
                    # Ενημέρωση συνολικού πορτοφολιού
                    overall_balance += (pair_total_pnl_pct / 100) * config.PAPER_BALANCE

        if profitable_pairs_found == 0:
            logger.debug(f"Ελέγχθηκαν διαδοχικά {checked_pairs} νομίσματα, αλλά κανένα δεν είχε θετικό κέρδος με αυτή τη στρατηγική.")
            return {"error": f"Ελέγχθηκαν διαδοχικά {checked_pairs} νομίσματα, αλλά κανένα δεν είχε θετικό κέρδος με αυτή τη στρατηγική."}

        # Ταξινόμηση ιστορικού βάσει χρόνου εξόδου
        successful_pairs_history.sort(key=lambda x: x['exit_time'])
        overall_profit_pct = ((overall_balance - overall_initial) / overall_initial) * 100

        # Υπολογισμός των Advanced Metrics (Sharpe, Sortino, Drawdown κ.λπ.)
        advanced_metrics = calculate_advanced_metrics(
            trades_history=successful_pairs_history,
            initial_balance=overall_initial,
            timeframe=config.TIMEFRAME
        )

        return {
            "trades": total_trades, 
            "initial": overall_initial, 
            "final": round(overall_balance, 2),
            "profit": f"{overall_profit_pct:.2f}", 
            "pairs_summary": pairs_summary, 
            "history": successful_pairs_history,
            "pair_details": pair_details,
            "metrics": advanced_metrics,
            "msg": f"Βρέθηκαν {profitable_pairs_found} κερδοφόρα ζεύγη."
        }
        
    except Exception as e:
        import traceback
        # Η logger.exception καταγράφει ΑΥΤΟΜΑΤΑ και το σφάλμα ΚΑΙ ολόκληρο το Traceback στο αρχείο .log!
        logger.exception("❌ Κρίσιμο σφάλμα κατά την εκτέλεση του backtester:")
        print(f"❌ Κρίσιμο σφάλμα στον backtester: {e}")
        traceback.print_exc()
        return {"error": f"Κρίσιμο σφάλμα στον backtester: {str(e)}"}