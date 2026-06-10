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

        # --- Ζητάμε ΑΜΕΣΑ μια μεγάλη λίστα από το API βάσει όγκου 24h ---
        pool_size = config.SCANNER_PAIR_LIMIT
        top_pairs_pool = exchange.get_top_volume_pairs(limit=pool_size, quote_asset=config.BASE_CURRENCY)
        
        if not top_pairs_pool: 
            return {"error": "Δεν βρέθηκαν δεδομένα αγοράς."}

        # Φιλτράρισμα με βάση τη Blacklist ΠΡΙΝ ξεκινήσουμε το κατέβασμα ιστορικού
        blacklist = config.SYMBOL_FILTERS.get("blacklist", []) if hasattr(config, 'SYMBOL_FILTERS') else []
        pairs_to_check = [p for p in top_pairs_pool if p not in blacklist]

        # =========================================================================
        # 👑 GLOBAL MARKET FILTER: BITCOIN UPTREND CHECK (με TrendCache - 1d & 4h)
        # =========================================================================
        trend_cache = TrendCache(exchange)

        # Το btc_filter_dict έχει πλέον keys ως datetime (ανά 4ωρο)
        btc_filter_dict = trend_cache.get_btc_trend_filter(start_date, end_date)

        # Φτιάχνουμε μια λίστα με τα sorted timestamps του BTC για binary search / fallback
        btc_timestamps = sorted(list(btc_filter_dict.keys()))

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
            
            in_position = False
            entry_price = 0.0
            entry_time = None
            position_high = 0.0
            trades = []

            # Προσομοίωση candle-by-candle
            for idx, row in df.iterrows():
                current_time = row['timestamp']
                current_close = float(row['close'])
                current_high = float(row['high'])
                current_low = float(row['low'])

                # Έλεγχος αν το BTC ήταν σε Uptrend τη συγκεκριμένη στιγμή (κερί)
                # Το πεδίο έχει ήδη υπολογιστεί σωστά και πάρει την τιμή από το 4h/1d cache
                is_btc_bullish = bool(row['is_btc_bullish'])
                
                if in_position:
                    # Ενημέρωση του υψηλότερου σημείου για το Trailing Stop
                    if current_high > position_high:
                        position_high = current_high
                        
                    exit_trade = False
                    exit_reason = ""
                    exit_price = current_close
                    
                    # 1. Έλεγχος Fixed Stop Loss
                    stop_loss_pct = getattr(config, 'STOP_LOSS_PCT', 0.05)
                    sl_price = entry_price * (1 - stop_loss_pct)
                    if current_low <= sl_price:
                        exit_trade = True
                        exit_reason = "Stop Loss"
                        exit_price = sl_price
                        
                    # 2. Έλεγχος Fixed Take Profit
                    take_profit_pct = getattr(config, 'TAKE_PROFIT_PCT', 0.10)
                    tp_price = entry_price * (1 + take_profit_pct)
                    if not exit_trade and current_high >= tp_price:
                        exit_trade = True
                        exit_reason = "Take Profit"
                        exit_price = tp_price
                        
                    # 3. Έλεγχος Trailing Stop (Percent ή ATR)
                    ts_config = getattr(config, 'TRAILING_STOP_CONFIG', {})
                    if not exit_trade and ts_config.get('enabled', False):
                        activation_pct = ts_config.get('activation_pct', 0.01)
                        trail_pct = ts_config.get('trail_pct', 0.03)
                        
                        # Ενεργοποίηση μόνο αν πιάσαμε το ελάχιστο κέρδος ενεργοποίησης
                        if (position_high - entry_price) / entry_price >= activation_pct:
                            ts_price = position_high * (1 - trail_pct)
                            if current_low <= ts_price:
                                exit_trade = True
                                exit_reason = "Trailing Stop"
                                exit_price = max(ts_price, current_close)
                                
                    # 4. Έλεγχος Τεχνικού Σήματος Έξοδου από τη Στρατηγική
                    if not exit_trade and row.get('signal') == -1:
                        exit_trade = True
                        exit_reason = "Indicator Exit Signal"
                        exit_price = current_close
                        
                    # Εκτέλεση Έξοδου
                    if exit_trade:
                        pnl_pct = ((exit_price - entry_price) / entry_price) * 100
                        trades.append({
                            "symbol": symbol,
                            "entry_time": entry_time,
                            "entry_price": entry_price,
                            "exit_time": current_time,
                            "exit_price": exit_price,
                            "pnl_pct": pnl_pct,
                            "reason": exit_reason
                        })
                        in_position = False
                        
                else:
                    # 🎯 ΚΡΙΣΙΜΟ ΦΙΛΤΡΟ: Αν το BTC δεν είναι σε Uptrend, απαγορεύεται η τεχνική ανάλυση / αγορά
                    if not is_btc_bullish:
                        continue
                        
                    # Έλεγχος Σήματος Αγοράς
                    if row.get('signal') == 1:
                        in_position = True
                        entry_price = current_close
                        entry_time = current_time
                        position_high = current_high
                        
            # Force close αν έμεινε ανοιχτή θέση στο τέλος του ιστορικού
            if in_position:
                last_row = df.iloc[-1]
                exit_price = float(last_row['close'])
                pnl_pct = ((exit_price - entry_price) / entry_price) * 100
                trades.append({
                    "symbol": symbol,
                    "entry_time": entry_time,
                    "entry_price": entry_price,
                    "exit_time": last_row['timestamp'],
                    "exit_price": exit_price,
                    "pnl_pct": pnl_pct,
                    "reason": "Force Close (End of Backtest)"
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