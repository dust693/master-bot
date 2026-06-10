# =============================================================================
# technical_analysis.py
# =============================================================================
# Αυτό το module είναι υπεύθυνο για τον υπολογισμό ΟΛΩΝ των τεχνικών δεικτών
# του trading bot. Καλείται από τον backtester και τον paper/live trader.
#
# Βασική λογική:
#   - Δέχεται ένα DataFrame με OHLCV κεριά (open, high, low, close, volume)
#   - Δέχεται το strategy_cfg (dict) με τις ρυθμίσεις που έχει ορίσει ο χρήστης στο UI
#   - Για κάθε ενεργό δείκτη, υπολογίζει μια στήλη "_signal" στο DataFrame:
#       +1 = σήμα ΑΓΟΡΑΣ (bullish)
#       -1 = σήμα ΠΩΛΗΣΗΣ (bearish)
#        0 = ουδέτερο / κανένα σήμα
#   - Επιστρέφει το εμπλουτισμένο DataFrame με όλες τις στήλες δεικτών + signals
#
# Κάθε δείκτης τυλίγεται σε try/except ώστε αν αποτύχει ένας να μην
# καταρρεύσει ολόκληρος ο υπολογισμός.
# =============================================================================

import pandas as pd          # Βιβλιοθήκη για DataFrames (πίνακες δεδομένων)
import pandas_ta as ta        # Βιβλιοθήκη τεχνικής ανάλυσης που επεκτείνει το pandas
import config
from utils.logger import logger

def calculate_all_indicators(df: pd.DataFrame, strategy_cfg: dict) -> pd.DataFrame:
    """Υπολογίζει τους δείκτες συγχρονισμένα με τις επιλογές του UI & Config."""

    # --- ΠΡΟΚΑΤΑΡΚΤΙΚΟΣ ΕΛΕΓΧΟΣ ---
    # Αν το DataFrame είναι άδειο ή έχει λιγότερα από 50 κεριά,
    # επιστρέφει αμέσως χωρίς υπολογισμό (δεν υπάρχουν αρκετά δεδομένα
    # για να παράγουν αξιόπιστα αποτελέσματα οι δείκτες).
    if df.empty or len(df) < config.MIN_CANDLES_REQUIRED:
        return df

    # ==========================================
    # 1. TREND FOLLOWERS
    # ==========================================
    # Δείκτες που παρακολουθούν την κατεύθυνση της τάσης (ανοδική / καθοδική).
    # Λειτουργούν καλά σε trending αγορές, δίνουν ψεύτικα σήματα σε sideways.

    # ------------------------------------------
    # SMA - Simple Moving Average (Απλός ΚΜΟ)
    # ------------------------------------------
    # Υπολογίζει τον αριθμητικό μέσο των τελευταίων N τιμών κλεισίματος.
    # Είναι ο πιο βασικός δείκτης τάσης — αργός και χωρίς βαρύτητα στα πρόσφατα κεριά.
    if strategy_cfg.get("SMA", {}).get("enabled"):
        try:
            cfg = strategy_cfg["SMA"]

            # Φορτώνει τις παραμέτρους από το config (με fallback στις default τιμές)
            # fast_period: περίοδος γρήγορου ΚΜΟ (π.χ. 10 κεριά)
            # slow_period: περίοδος αργού ΚΜΟ (π.χ. 50 κεριά)
            # signal: τύπος σήματος που θα χρησιμοποιηθεί (βλ. παρακάτω)
            fast, slow, sig_type = cfg.get("fast_period", 10), cfg.get("slow_period", 50), cfg.get("signal", "crossover")

            # Υπολογίζει και προσθέτει στο df τις στήλες SMA_{fast} και SMA_{slow}
            # append=True σημαίνει ότι η στήλη προστίθεται απευθείας στο DataFrame
            df.ta.sma(length=fast, append=True)
            df.ta.sma(length=slow, append=True)

            # Αρχικοποίηση στήλης σήματος με 0 (ουδέτερο)
            df['SMA_signal'] = 0

            if sig_type == "crossover":
                # CROSSOVER: Σήμα αγοράς όταν η γρήγορη SMA περνά ΠΑΝΩ από την αργή (golden cross)
                # Σήμα πώλησης όταν περνά ΚΑΤΩ (death cross)
                # Χρήση .shift(1) για να συγκρίνουμε με την ΠΡΟΗΓΟΥΜΕΝΗ τιμή → εντοπίζει τη στιγμή της σταύρωσης
                df.loc[(df[f'SMA_{fast}'] > df[f'SMA_{slow}']) & (df[f'SMA_{fast}'].shift(1) <= df[f'SMA_{slow}'].shift(1)), 'SMA_signal'] = 1
                df.loc[(df[f'SMA_{fast}'] < df[f'SMA_{slow}']) & (df[f'SMA_{fast}'].shift(1) >= df[f'SMA_{slow}'].shift(1)), 'SMA_signal'] = -1

            elif sig_type == "price_above":
                # PRICE ABOVE SLOW: Η τιμή πάνω από την αργή SMA = bullish περιβάλλον
                # Συνεχές σήμα (όχι crossover) — παραμένει +1 όσο η τιμή είναι πάνω
                df.loc[df['close'] > df[f'SMA_{slow}'], 'SMA_signal'] = 1
                df.loc[df['close'] < df[f'SMA_{slow}'], 'SMA_signal'] = -1

            elif sig_type == "price_above_fast":
                # PRICE ABOVE FAST: Πιο ευαίσθητη παραλλαγή — σύγκριση με τη γρήγορη SMA
                df.loc[df['close'] > df[f'SMA_{fast}'], 'SMA_signal'] = 1
                df.loc[df['close'] < df[f'SMA_{fast}'], 'SMA_signal'] = -1

            _s = df['SMA_signal'].iloc[-1]
            logger.debug(f"[SMA] fast={fast} | slow={slow} | signal_type={sig_type} | Αποτέλεσμα: {'BUY ✅' if _s == 1 else 'SELL 🔴' if _s == -1 else 'NEUTRAL ⚪'}")
        except Exception: pass  # Αποτυχία δείκτη δεν διακόπτει την υπόλοιπη εκτέλεση

    # ------------------------------------------
    # EMA - Exponential Moving Average (Εκθετικός ΚΜΟ)
    # ------------------------------------------
    # Παρόμοιος με SMA αλλά δίνει μεγαλύτερη βαρύτητα στα πρόσφατα κεριά.
    # Αντιδρά πιο γρήγορα στις αλλαγές τιμής από ότι η SMA.
    if strategy_cfg.get("EMA", {}).get("enabled"):
        try:
            cfg = strategy_cfg["EMA"]

            # fast_period: γρήγορη EMA (default 9), slow_period: αργή EMA (default 21)
            fast, slow, sig_type = cfg.get("fast_period", 9), cfg.get("slow_period", 21), cfg.get("signal", "crossover")

            # Υπολογισμός και append EMA_{fast} και EMA_{slow} στο df
            df.ta.ema(length=fast, append=True)
            df.ta.ema(length=slow, append=True)
            df['EMA_signal'] = 0

            if sig_type == "crossover":
                # Ίδια λογική crossover με SMA — εντοπίζει τη στιγμή της σταύρωσης
                df.loc[(df[f'EMA_{fast}'] > df[f'EMA_{slow}']) & (df[f'EMA_{fast}'].shift(1) <= df[f'EMA_{slow}'].shift(1)), 'EMA_signal'] = 1
                df.loc[(df[f'EMA_{fast}'] < df[f'EMA_{slow}']) & (df[f'EMA_{fast}'].shift(1) >= df[f'EMA_{slow}'].shift(1)), 'EMA_signal'] = -1

            elif sig_type == "price_above":
                # Τιμή πάνω/κάτω από αργή EMA
                df.loc[df['close'] > df[f'EMA_{slow}'], 'EMA_signal'] = 1
                df.loc[df['close'] < df[f'EMA_{slow}'], 'EMA_signal'] = -1

            elif sig_type == "price_above_fast":
                # Τιμή πάνω/κάτω από γρήγορη EMA (πιο ευαίσθητο)
                df.loc[df['close'] > df[f'EMA_{fast}'], 'EMA_signal'] = 1
                df.loc[df['close'] < df[f'EMA_{fast}'], 'EMA_signal'] = -1

            _s = df['EMA_signal'].iloc[-1]
            logger.debug(f"[EMA] fast={fast} | slow={slow} | signal_type={sig_type} | Αποτέλεσμα: {'BUY ✅' if _s == 1 else 'SELL 🔴' if _s == -1 else 'NEUTRAL ⚪'}")
        except Exception: pass

    # ------------------------------------------
    # DEMA - Double Exponential Moving Average
    # ------------------------------------------
    # DEMA = 2*EMA - EMA(EMA). Αφαιρεί το "lag" της EMA εφαρμόζοντας
    # εκθετική εξομάλυνση δύο φορές. Πιο γρήγορος από EMA αλλά πιο θορυβώδης.
    if strategy_cfg.get("DEMA", {}).get("enabled"):
        try:
            cfg = strategy_cfg["DEMA"]

            # Υπολογίζει DEMA και append period
            dema = df.ta.dema(length=cfg.get("period", 21), append=True)
            sig_type = cfg.get("signal", "price_above")
            df['DEMA_signal'] = 0

            if sig_type == "price_above":
                # Τιμή πάνω/κάτω από DEMA = bullish/bearish
                df.loc[df['close'] > dema, 'DEMA_signal'] = 1
                df.loc[df['close'] < dema, 'DEMA_signal'] = -1

            elif sig_type == "slope":
                # SLOPE: εξετάζει την κλίση της DEMA (ανεβαίνει; κατεβαίνει;)
                # .diff() = διαφορά κάθε τιμής από την προηγούμενη (1η παράγωγος)
                # .rolling(slope_period).mean() = εξομαλύνει τη μεταβολή σε N περιόδους
                # slope > 0 = DEMA ανεβαίνει → bullish, slope < 0 = κατεβαίνει → bearish
                slope_period = cfg.get("slope_period", 3)
                slope = dema.diff().rolling(slope_period).mean()
                df.loc[slope > 0, 'DEMA_signal'] = 1
                df.loc[slope < 0, 'DEMA_signal'] = -1

            elif sig_type == "crossover":
                # CROSSOVER τιμής με DEMA: εντοπίζει τη στιγμή που η τιμή περνά τη γραμμή
                df.loc[(df['close'] > dema) & (df['close'].shift(1) <= dema.shift(1)), 'DEMA_signal'] = 1
                df.loc[(df['close'] < dema) & (df['close'].shift(1) >= dema.shift(1)), 'DEMA_signal'] = -1

            _s = df['DEMA_signal'].iloc[-1]
            logger.debug(f"[DEMA] period={cfg.get('period', 21)} | signal_type={sig_type} | Αποτέλεσμα: {'BUY ✅' if _s == 1 else 'SELL 🔴' if _s == -1 else 'NEUTRAL ⚪'}")
        except Exception: pass

    # ------------------------------------------
    # TEMA - Triple Exponential Moving Average
    # ------------------------------------------
    # TEMA = 3*EMA - 3*EMA(EMA) + EMA(EMA(EMA)). Τριπλή εκθετική εξομάλυνση.
    # Ακόμα πιο γρήγορος από DEMA, ελαχιστοποιεί το lag, αλλά και πιο ευαίσθητος σε θόρυβο.
    if strategy_cfg.get("TEMA", {}).get("enabled"):
        try:
            cfg = strategy_cfg["TEMA"]
            tema = df.ta.tema(length=cfg.get("period", 21), append=True)
            sig_type = cfg.get("signal", "price_above")
            df['TEMA_signal'] = 0

            if sig_type == "price_above":
                # Τιμή πάνω/κάτω από TEMA
                df.loc[df['close'] > tema, 'TEMA_signal'] = 1
                df.loc[df['close'] < tema, 'TEMA_signal'] = -1

            elif sig_type == "slope":
                # Κλίση TEMA — ίδια λογική με DEMA slope
                slope = tema.diff().rolling(cfg.get("slope_period", 3)).mean()
                df.loc[slope > 0, 'TEMA_signal'] = 1
                df.loc[slope < 0, 'TEMA_signal'] = -1

            _s = df['TEMA_signal'].iloc[-1]
            logger.debug(f"[TEMA] period={cfg.get('period', 21)} | signal_type={sig_type} | Αποτέλεσμα: {'BUY ✅' if _s == 1 else 'SELL 🔴' if _s == -1 else 'NEUTRAL ⚪'}")
        except Exception: pass

    # ------------------------------------------
    # WMA - Weighted Moving Average (Σταθμισμένος ΚΜΟ)
    # ------------------------------------------
    # Δίνει γραμμικά αυξανόμενη βαρύτητα στα πρόσφατα κεριά
    # (το τελευταίο κερί έχει βάρος N, το προτελευταίο N-1, κ.ο.κ.).
    # Πιο ευαίσθητος από SMA, λιγότερο "εκθετικός" από EMA.
    if strategy_cfg.get("WMA", {}).get("enabled"):
        try:
            cfg = strategy_cfg["WMA"]
            fast, slow, sig_type = cfg.get("fast_period", 9), cfg.get("slow_period", 21), cfg.get("signal", "crossover")

            fast_wma = df.ta.wma(length=fast, append=True)
            slow_wma = df.ta.wma(length=slow, append=True)
            df['WMA_signal'] = 0

            if sig_type == "crossover":
                # Σταύρωση γρήγορης πάνω/κάτω από αργή WMA
                df.loc[(fast_wma > slow_wma) & (fast_wma.shift(1) <= slow_wma.shift(1)), 'WMA_signal'] = 1
                df.loc[(fast_wma < slow_wma) & (fast_wma.shift(1) >= slow_wma.shift(1)), 'WMA_signal'] = -1

            elif sig_type == "price_above":
                # Τιμή πάνω/κάτω από αργή WMA
                df.loc[df['close'] > slow_wma, 'WMA_signal'] = 1
                df.loc[df['close'] < slow_wma, 'WMA_signal'] = -1

            _s = df['WMA_signal'].iloc[-1]
            logger.debug(f"[WMA] fast={fast} | slow={slow} | signal_type={sig_type} | Αποτέλεσμα: {'BUY ✅' if _s == 1 else 'SELL 🔴' if _s == -1 else 'NEUTRAL ⚪'}")
        except Exception: pass

    # ------------------------------------------
    # VWAP - Volume Weighted Average Price
    # ------------------------------------------
    # Ο VWAP είναι ο μέσος όρος τιμής σταθμισμένος από τον ΟΓΚΟ συναλλαγών.
    # Χρησιμοποιείται ευρύτατα από institutional traders ως σημείο αναφοράς.
    # Τιμή > VWAP = η αγορά είναι bullish (αγοράζουν σε υψηλότερες τιμές από τον μέσο)
    if strategy_cfg.get("VWAP", {}).get("enabled"):
        try:
            # Ο VWAP χρειάζεται columns: high, low, close, volume (τα έχει το df)
            vwap = df.ta.vwap(append=True)
            sig_type = strategy_cfg["VWAP"].get("signal", "price_above")
            df['VWAP_signal'] = 0

            if sig_type == "price_above":
                # Κλασική χρήση: τιμή > VWAP = αγορά (θετικό momentum)
                df.loc[df['close'] > vwap, 'VWAP_signal'] = 1
                df.loc[df['close'] < vwap, 'VWAP_signal'] = -1

            elif sig_type == "price_below": # Αντίστροφο
                # Αντίστροφη λογική (mean reversion): τιμή κάτω από VWAP = αναμένεται επιστροφή πάνω
                df.loc[df['close'] < vwap, 'VWAP_signal'] = 1
                df.loc[df['close'] > vwap, 'VWAP_signal'] = -1

            _s = df['VWAP_signal'].iloc[-1]
            logger.debug(f"[VWAP] signal_type={sig_type} | close={df['close'].iloc[-1]:.4f} | Αποτέλεσμα: {'BUY ✅' if _s == 1 else 'SELL 🔴' if _s == -1 else 'NEUTRAL ⚪'}")
        except Exception: pass

    # ------------------------------------------
    # ICHIMOKU - Ichimoku Kinko Hyo (Σύννεφο Ichimoku)
    # ------------------------------------------
    # Σύνθετο σύστημα τεχνικής ανάλυσης με 5 γραμμές που δείχνει ταυτόχρονα
    # τάση, momentum, support και resistance. Αποτελείται από:
    #   Tenkan-sen (Conversion Line): ΚΜΟ high/low 9 περιόδων
    #   Kijun-sen  (Base Line)      : ΚΜΟ high/low 26 περιόδων
    #   Senkou Span A               : μέσος Tenkan+Kijun (μετατοπισμένος 26 μπροστά)
    #   Senkou Span B               : ΚΜΟ high/low 52 περιόδων (μετατοπισμένος 26 μπροστά)
    #   Kumo (Cloud)                : η περιοχή μεταξύ Span A και Span B
    if strategy_cfg.get("ICHIMOKU", {}).get("enabled"):
        try:
            cfg = strategy_cfg["ICHIMOKU"]
            sig_type = cfg.get("signal", "cloud_breakout")

            # Υπολογισμός Ichimoku και append όλων των στηλών στο df
            df.ta.ichimoku(tenkan=cfg.get("tenkan", 9), kijun=cfg.get("kijun", 26), senkou=cfg.get("senkou_b", 52), append=True)
            
            # --- Δυναμική εύρεση στηλών Ichimoku ---
            # Το pandas_ta δημιουργεί στήλες με δυναμικά ονόματα (π.χ. ITS_9, IKS_26 κ.λπ.)
            # ανάλογα με τις παραμέτρους. Το [-1] παίρνει την τελευταία ταιριαστή στήλη
            # (χρήσιμο αν έχουν τρέξει πολλές φορές Ichimoku με διαφορετικές παραμέτρους).
            tenkan_col = [c for c in df.columns if c.startswith('ITS')][-1]  # Tenkan-sen
            kijun_col  = [c for c in df.columns if c.startswith('IKS')][-1]  # Kijun-sen
            senkou_a   = [c for c in df.columns if c.startswith('ISA')][-1]  # Senkou Span A
            senkou_b   = [c for c in df.columns if c.startswith('ISB')][-1]  # Senkou Span B
            
            df['ICHI_signal'] = 0

            if sig_type == "cloud_breakout":
                # CLOUD BREAKOUT: Σήμα αγοράς αν η τιμή είναι ΠΑΝΩ από το σύννεφο
                # (πάνω και από τα δύο Span A και Span B → bullish zone)
                # max(axis=1) = το ανώτατο όριο του cloud, min(axis=1) = κατώτατο
                df.loc[df['close'] > df[[senkou_a, senkou_b]].max(axis=1), 'ICHI_signal'] = 1
                df.loc[df['close'] < df[[senkou_a, senkou_b]].min(axis=1), 'ICHI_signal'] = -1

            elif sig_type == "tenkan_kijun_cross":
                # TK CROSS: Σήμα αγοράς όταν Tenkan περνά ΠΑΝΩ από Kijun
                # (παρόμοιο με golden cross αλλά σε πιο βραχυπρόθεσμο ορίζοντα)
                df.loc[(df[tenkan_col] > df[kijun_col]) & (df[tenkan_col].shift(1) <= df[kijun_col].shift(1)), 'ICHI_signal'] = 1
                df.loc[(df[tenkan_col] < df[kijun_col]) & (df[tenkan_col].shift(1) >= df[kijun_col].shift(1)), 'ICHI_signal'] = -1

            elif sig_type == "cloud_twist":
                # CLOUD TWIST (Kumo Twist): Σήμα αγοράς όταν το σύννεφο "ανατρέπεται"
                # δηλαδή το Span A περνά ΠΑΝΩ από το Span B (bullish cloud)
                # Αυτό δείχνει ότι το μελλοντικό cloud γίνεται bullish
                df.loc[(df[senkou_a] > df[senkou_b]) & (df[senkou_a].shift(1) <= df[senkou_b].shift(1)), 'ICHI_signal'] = 1
                df.loc[(df[senkou_a] < df[senkou_b]) & (df[senkou_a].shift(1) >= df[senkou_b].shift(1)), 'ICHI_signal'] = -1

            _s = df['ICHI_signal'].iloc[-1]
            logger.debug(f"[ICHIMOKU] tenkan={cfg.get('tenkan', 9)} | kijun={cfg.get('kijun', 26)} | senkou_b={cfg.get('senkou_b', 52)} | signal_type={sig_type} | Αποτέλεσμα: {'BUY ✅' if _s == 1 else 'SELL 🔴' if _s == -1 else 'NEUTRAL ⚪'}")
        except Exception: pass

    # ==========================================
    # 2. MOMENTUM / OSCILLATORS
    # ==========================================
    # Δείκτες που μετρούν την ταχύτητα/δύναμη της κίνησης της τιμής.
    # Κυμαίνονται συνήθως σε εύρος (π.χ. 0-100) και εντοπίζουν
    # υπεραγορασμένες (overbought) και υπερπωλημένες (oversold) καταστάσεις.

    # ------------------------------------------
    # RSI - Relative Strength Index
    # ------------------------------------------
    # Μετρά την ταχύτητα και μέγεθος των κινήσεων τιμής σε εύρος 0-100.
    # RSI < 30 = υπερπωλημένο (oversold) → πιθανή ανατροπή προς τα πάνω
    # RSI > 70 = υπεραγορασμένο (overbought) → πιθανή ανατροπή προς τα κάτω
    if strategy_cfg.get("RSI", {}).get("enabled"):
        try:
            cfg = strategy_cfg["RSI"]

            # Υπολογίζει RSI_{period} και το κάνει append στο df
            rsi = df.ta.rsi(length=cfg.get("period", 14), append=True)
            df['RSI_signal'] = 0

            # oversold: κάτω από αυτό το όριο δίνει +1 (αγορά)
            # overbought: πάνω από αυτό δίνει -1 (πώληση)
            df.loc[rsi < cfg.get("oversold", 30), 'RSI_signal'] = 1
            df.loc[rsi > cfg.get("overbought", 70), 'RSI_signal'] = -1

            _s = df['RSI_signal'].iloc[-1]
            _rsi_val = round(rsi.iloc[-1], 2) if rsi is not None else "N/A"
            logger.debug(f"[RSI] period={cfg.get('period', 14)} | oversold={cfg.get('oversold', 30)} | overbought={cfg.get('overbought', 70)} | RSI={_rsi_val} | Αποτέλεσμα: {'BUY ✅' if _s == 1 else 'SELL 🔴' if _s == -1 else 'NEUTRAL ⚪'}")
        except Exception: pass

    # ------------------------------------------
    # MACD - Moving Average Convergence/Divergence
    # ------------------------------------------
    # Αποτελείται από:
    #   MACD line   : EMA(fast) - EMA(slow)  → μετράει momentum
    #   Signal line : EMA(signal_period) του MACD line → εξομαλυντής
    #   Histogram   : MACD line - Signal line → δείχνει αλλαγή δύναμης
    if strategy_cfg.get("MACD", {}).get("enabled"):
        try:
            cfg = strategy_cfg["MACD"]

            # Υπολογίζει MACD και επιστρέφει DataFrame με 3 στήλες:
            # [MACD_{fast}_{slow}_{signal}, MACDh_{...}, MACDs_{...}]
            macd = df.ta.macd(fast=cfg.get("fast", 12), slow=cfg.get("slow", 26), signal=cfg.get("signal_line", 9), append=True)
            sig_type = cfg.get("signal_type", "crossover")
            df['MACD_pos_signal'] = 0

            if macd is not None and not macd.empty:
                # Παίρνει τα ονόματα των 3 στηλών δυναμικά από το αποτέλεσμα
                macd_line, hist_line, signal_line = macd.columns[0], macd.columns[1], macd.columns[2]

                if sig_type == "crossover":
                    # CROSSOVER: σήμα αγοράς όταν η MACD line περνά ΠΑΝΩ από το signal line
                    # (η δύναμη του uptrend επιταχύνεται)
                    df.loc[(macd[macd_line] > macd[signal_line]) & (macd[macd_line].shift(1) <= macd[signal_line].shift(1)), 'MACD_pos_signal'] = 1
                    df.loc[(macd[macd_line] < macd[signal_line]) & (macd[macd_line].shift(1) >= macd[signal_line].shift(1)), 'MACD_pos_signal'] = -1

                elif sig_type == "histogram_zero":
                    # HISTOGRAM ZERO CROSS: σήμα αγοράς όταν το histogram περνά από αρνητικό σε θετικό
                    # (το momentum αλλάζει κατεύθυνση)
                    df.loc[(macd[hist_line] > 0) & (macd[hist_line].shift(1) <= 0), 'MACD_pos_signal'] = 1
                    df.loc[(macd[hist_line] < 0) & (macd[hist_line].shift(1) >= 0), 'MACD_pos_signal'] = -1

                elif sig_type == "histogram_increasing":
                    # HISTOGRAM INCREASING: σήμα αγοράς όταν το histogram μεγαλώνει για 2 συνεχόμενες περιόδους
                    # (επιτάχυνση του bullish momentum)
                    # shift(1) = προηγούμενη τιμή, shift(2) = δύο περιόδους πριν
                    df.loc[(macd[hist_line] > macd[hist_line].shift(1)) & (macd[hist_line].shift(1) > macd[hist_line].shift(2)), 'MACD_pos_signal'] = 1

            _s = df['MACD_pos_signal'].iloc[-1]
            logger.debug(f"[MACD] fast={cfg.get('fast', 12)} | slow={cfg.get('slow', 26)} | signal_line={cfg.get('signal_line', 9)} | signal_type={sig_type} | Αποτέλεσμα: {'BUY ✅' if _s == 1 else 'SELL 🔴' if _s == -1 else 'NEUTRAL ⚪'}")
        except Exception: pass

    # ------------------------------------------
    # STOCHASTIC Oscillator
    # ------------------------------------------
    # Συγκρίνει την τρέχουσα τιμή κλεισίματος με το εύρος high/low των τελευταίων N κεριών.
    # Δίνει τιμές 0-100. Ιδανικός για εντοπισμό υπεραγορασμένων/υπερπωλημένων συνθηκών.
    # Αποτελείται από %K line (γρήγορη) και %D line (εξομαλυμένη %K).
    if strategy_cfg.get("STOCHASTIC", {}).get("enabled"):
        try:
            cfg = strategy_cfg["STOCHASTIC"]

            # k_period: παράθυρο υπολογισμού %K
            # d_period: εξομάλυνση για %D
            # smooth_k: επιπλέον εξομάλυνση του %K
            stoch = df.ta.stoch(k=cfg.get("k_period",14), d=cfg.get("d_period",3), smooth_k=cfg.get("smooth_k",3), append=True)

            # Χρησιμοποιεί μόνο το %K (πρώτη στήλη) για τα σήματα
            k_line = stoch[stoch.columns[0]]
            df['STOCH_signal'] = 0

            # oversold < 20 → σήμα αγοράς, overbought > 80 → σήμα πώλησης
            df.loc[k_line < cfg.get("oversold", 20), 'STOCH_signal'] = 1
            df.loc[k_line > cfg.get("overbought", 80), 'STOCH_signal'] = -1

            _s = df['STOCH_signal'].iloc[-1]
            _k_val = round(k_line.iloc[-1], 2) if k_line is not None else "N/A"
            logger.debug(f"[STOCHASTIC] k={cfg.get('k_period', 14)} | d={cfg.get('d_period', 3)} | oversold={cfg.get('oversold', 20)} | overbought={cfg.get('overbought', 80)} | %%K={_k_val} | Αποτέλεσμα: {'BUY ✅' if _s == 1 else 'SELL 🔴' if _s == -1 else 'NEUTRAL ⚪'}")
        except Exception: pass

    # ------------------------------------------
    # CCI - Commodity Channel Index
    # ------------------------------------------
    # Μετρά πόσο η τρέχουσα τιμή αποκλίνει από τον στατιστικό μέσο.
    # Θεωρητικά κυμαίνεται άπειρα αλλά συνήθως μεταξύ -200 και +200.
    # CCI < -100 = oversold (υπερπωλημένο), CCI > +100 = overbought
    if strategy_cfg.get("CCI", {}).get("enabled"):
        try:
            cci = df.ta.cci(length=strategy_cfg["CCI"].get("period", 20), append=True)
            df['CCI_signal'] = 0

            # Κάτω από -100 → αγορά (υπερπωλημένο), πάνω από +100 → πώληση
            df.loc[cci < strategy_cfg["CCI"].get("oversold", -100), 'CCI_signal'] = 1
            df.loc[cci > strategy_cfg["CCI"].get("overbought", 100), 'CCI_signal'] = -1

            _s = df['CCI_signal'].iloc[-1]
            _cci_val = round(cci.iloc[-1], 2) if cci is not None else "N/A"
            logger.debug(f"[CCI] period={strategy_cfg['CCI'].get('period', 20)} | oversold={strategy_cfg['CCI'].get('oversold', -100)} | overbought={strategy_cfg['CCI'].get('overbought', 100)} | CCI={_cci_val} | Αποτέλεσμα: {'BUY ✅' if _s == 1 else 'SELL 🔴' if _s == -1 else 'NEUTRAL ⚪'}")
        except Exception: pass

    # ------------------------------------------
    # WILLIAMS %R
    # ------------------------------------------
    # Παρόμοιος με Stochastic αλλά σε κλίμακα 0 έως -100 (ανεστραμμένο).
    # Τιμές κοντά στο 0 = overbought, κοντά στο -100 = oversold.
    # -80 = όριο oversold (αγορά), -20 = όριο overbought (πώληση)
    if strategy_cfg.get("WILLIAMS_R", {}).get("enabled"):
        try:
            willr = df.ta.willr(length=strategy_cfg["WILLIAMS_R"].get("period", 14), append=True)
            df['WILLIAMS_signal'] = 0

            # Κάτω από -80 → oversold → αγορά
            # Πάνω από -20 → overbought → πώληση
            df.loc[willr < strategy_cfg["WILLIAMS_R"].get("oversold", -80), 'WILLIAMS_signal'] = 1
            df.loc[willr > strategy_cfg["WILLIAMS_R"].get("overbought", -20), 'WILLIAMS_signal'] = -1

            _s = df['WILLIAMS_signal'].iloc[-1]
            _w_val = round(willr.iloc[-1], 2) if willr is not None else "N/A"
            logger.debug(f"[WILLIAMS_R] period={strategy_cfg['WILLIAMS_R'].get('period', 14)} | oversold={strategy_cfg['WILLIAMS_R'].get('oversold', -80)} | overbought={strategy_cfg['WILLIAMS_R'].get('overbought', -20)} | %%R={_w_val} | Αποτέλεσμα: {'BUY ✅' if _s == 1 else 'SELL 🔴' if _s == -1 else 'NEUTRAL ⚪'}")
        except Exception: pass

    # ------------------------------------------
    # MFI - Money Flow Index
    # ------------------------------------------
    # Ονομάζεται και "Volume-weighted RSI". Συνδυάζει τιμή ΚΑΙ όγκο για να
    # μετρήσει αν χρήμα εισρέει ή εκρέει από ένα asset.
    # Κλίμακα 0-100. MFI < 20 = oversold, MFI > 80 = overbought.
    if strategy_cfg.get("MFI", {}).get("enabled"):
        try:
            mfi = df.ta.mfi(length=strategy_cfg["MFI"].get("period", 14), append=True)
            df['MFI_signal'] = 0

            # Ίδια λογική με RSI: oversold → αγορά, overbought → πώληση
            df.loc[mfi < strategy_cfg["MFI"].get("oversold", 20), 'MFI_signal'] = 1
            df.loc[mfi > strategy_cfg["MFI"].get("overbought", 80), 'MFI_signal'] = -1

            _s = df['MFI_signal'].iloc[-1]
            _mfi_val = round(mfi.iloc[-1], 2) if mfi is not None else "N/A"
            logger.debug(f"[MFI] period={strategy_cfg['MFI'].get('period', 14)} | oversold={strategy_cfg['MFI'].get('oversold', 20)} | overbought={strategy_cfg['MFI'].get('overbought', 80)} | MFI={_mfi_val} | Αποτέλεσμα: {'BUY ✅' if _s == 1 else 'SELL 🔴' if _s == -1 else 'NEUTRAL ⚪'}")
        except Exception: pass

    # ------------------------------------------
    # ROC - Rate of Change
    # ------------------------------------------
    # Μετρά το ποσοστό % μεταβολής της τιμής σε N περιόδους.
    # ROC = (close_τώρα - close_N_πριν) / close_N_πριν * 100
    # Θετικό ROC = ανοδική ορμή, αρνητικό = καθοδική.
    if strategy_cfg.get("ROC", {}).get("enabled"):
        try:
            cfg = strategy_cfg["ROC"]
            roc = df.ta.roc(length=cfg.get("period", 12), append=True)
            sig_type = cfg.get("signal", "crossover_zero")
            df['ROC_signal'] = 0

            if sig_type == "above_threshold":
                # THRESHOLD: σήμα αγοράς αν ROC > threshold (ισχυρό θετικό momentum)
                # σήμα πώλησης αν ROC < -threshold
                df.loc[roc > cfg.get("threshold", 0), 'ROC_signal'] = 1
                df.loc[roc < -cfg.get("threshold", 0), 'ROC_signal'] = -1

            elif sig_type == "crossover_zero":
                # ZERO CROSSOVER: σήμα αγοράς τη στιγμή που το ROC περνά από αρνητικό σε θετικό
                # (momentum αλλάζει κατεύθυνση — πιο επιλεκτικό από threshold)
                df.loc[(roc > 0) & (roc.shift(1) <= 0), 'ROC_signal'] = 1
                df.loc[(roc < 0) & (roc.shift(1) >= 0), 'ROC_signal'] = -1

            _s = df['ROC_signal'].iloc[-1]
            _roc_val = round(roc.iloc[-1], 4) if roc is not None else "N/A"
            logger.debug(f"[ROC] period={cfg.get('period', 12)} | signal_type={sig_type} | threshold={cfg.get('threshold', 0)} | ROC={_roc_val} | Αποτέλεσμα: {'BUY ✅' if _s == 1 else 'SELL 🔴' if _s == -1 else 'NEUTRAL ⚪'}")
        except Exception: pass

    # ==========================================
    # 3. VOLATILITY
    # ==========================================
    # Δείκτες που μετρούν πόσο "ζωντανή" είναι η αγορά — πόσο μεγάλες
    # είναι οι κινήσεις. Χρήσιμοι για εντοπισμό breakouts και squeezes.

    # ------------------------------------------
    # BOLLINGER BANDS (Μπάντες Bollinger)
    # ------------------------------------------
    # Τρεις γραμμές γύρω από μια SMA:
    #   Upper Band = SMA + (std_dev * τυπική απόκλιση)
    #   Middle Band = SMA(period)
    #   Lower Band = SMA - (std_dev * τυπική απόκλιση)
    # Όσο μεγαλύτερο το εύρος → μεγαλύτερη volatility.
    if strategy_cfg.get("BOLLINGER_BANDS", {}).get("enabled"):
        try:
            cfg = strategy_cfg["BOLLINGER_BANDS"]

            # Επιστρέφει DataFrame με στήλες: [BBL (lower), BBM (middle), BBU (upper), BBB (width), BBP (percent)]
            bb = df.ta.bbands(length=cfg.get("period", 20), std=cfg.get("std_dev", 2.0), append=True)
            sig_type = cfg.get("signal", "breakout")
            df['BB_signal'] = 0

            if bb is not None and not bb.empty:
                # columns[0] = lower band, columns[2] = upper band
                lower, upper = bb[bb.columns[0]], bb[bb.columns[2]]

                if sig_type == "breakout":
                    # BREAKOUT: τιμή πάνω από upper → ισχυρό bullish momentum
                    # τιμή κάτω από lower → ισχυρό bearish momentum
                    df.loc[df['close'] > upper, 'BB_signal'] = 1
                    df.loc[df['close'] < lower, 'BB_signal'] = -1

                elif sig_type == "mean_reversion":
                    # MEAN REVERSION (αντίστροφη λογική): τιμή κάτω από lower → αναμένεται επιστροφή πάνω
                    # Η τιμή θεωρείται "υπερβολικά χαμηλά" και αναμένεται διόρθωση
                    df.loc[df['close'] < lower, 'BB_signal'] = 1
                    df.loc[df['close'] > upper, 'BB_signal'] = -1

                elif sig_type == "squeeze":
                    # SQUEEZE: όταν το εύρος (upper-lower) ÷ τιμή είναι πολύ μικρό,
                    # η αγορά συμπιέζεται (consolidation) → αναμένεται έκρηξη κίνησης
                    # squeeze_threshold: κατώφλι κάτω από το οποίο θεωρούμε ότι υπάρχει squeeze
                    bb_width = (upper - lower) / df['close']
                    df.loc[bb_width < cfg.get("squeeze_threshold", 0.015), 'BB_signal'] = 1

            _s = df['BB_signal'].iloc[-1]
            logger.debug(f"[BOLLINGER_BANDS] period={cfg.get('period', 20)} | std_dev={cfg.get('std_dev', 2.0)} | signal_type={sig_type} | Αποτέλεσμα: {'BUY ✅' if _s == 1 else 'SELL 🔴' if _s == -1 else 'NEUTRAL ⚪'}")
        except Exception: pass

    # ------------------------------------------
    # ATR - Average True Range
    # ------------------------------------------
    # Μετρά τη μέση πραγματική κίνηση (εύρος) κάθε κεριού.
    # ΔΕΝ δίνει σήμα αγοράς/πώλησης — χρησιμοποιείται μόνο ως
    # βοηθητικός δείκτης για τον υπολογισμό δυναμικού trailing stop (ATR-based).
    if strategy_cfg.get("ATR", {}).get("enabled"):
        try:
            # Αποθηκεύει απευθείας ως στήλη 'ATR' στο df
            df['ATR'] = df.ta.atr(length=strategy_cfg["ATR"].get("period", 14))
            _atr_val = round(df['ATR'].iloc[-1], 6) if 'ATR' in df.columns else "N/A"
            logger.debug(f"[ATR] period={strategy_cfg['ATR'].get('period', 14)} | ATR={_atr_val} (χρησιμοποιείται για trailing stop, δεν παράγει signal)")
        except Exception: pass

    # ------------------------------------------
    # KELTNER CHANNELS (Κανάλια Keltner)
    # ------------------------------------------
    # Παρόμοια με Bollinger Bands αλλά χρησιμοποιεί ATR αντί για τυπική απόκλιση:
    #   Upper = EMA + (atr_multiplier * ATR)
    #   Lower = EMA - (atr_multiplier * ATR)
    # Λιγότερο "ευαίσθητα" από Bollinger — φιλτράρουν καλύτερα τον θόρυβο.
    if strategy_cfg.get("KELTNER", {}).get("enabled"):
        try:
            cfg = strategy_cfg["KELTNER"]

            # scalar = πολλαπλασιαστής ATR για το εύρος του καναλιού
            kc = df.ta.kc(length=cfg.get("period", 20), scalar=cfg.get("atr_multiplier", 2.0), append=True)
            sig_type = cfg.get("signal", "breakout")
            df['KC_signal'] = 0

            if kc is not None and not kc.empty:
                lower, upper = kc[kc.columns[0]], kc[kc.columns[2]]

                if sig_type == "breakout":
                    # Τιμή έξω από το κανάλι → breakout
                    df.loc[df['close'] > upper, 'KC_signal'] = 1
                    df.loc[df['close'] < lower, 'KC_signal'] = -1

                elif sig_type == "mean_reversion":
                    # Τιμή στα όρια του καναλιού → αναμένεται επιστροφή στο κέντρο
                    df.loc[df['close'] < lower, 'KC_signal'] = 1
                    df.loc[df['close'] > upper, 'KC_signal'] = -1

            _s = df['KC_signal'].iloc[-1]
            logger.debug(f"[KELTNER] period={cfg.get('period', 20)} | atr_multiplier={cfg.get('atr_multiplier', 2.0)} | signal_type={sig_type} | Αποτέλεσμα: {'BUY ✅' if _s == 1 else 'SELL 🔴' if _s == -1 else 'NEUTRAL ⚪'}")
        except Exception: pass

    # ------------------------------------------
    # DONCHIAN CHANNELS (Κανάλια Donchian)
    # ------------------------------------------
    # Δείχνει το υψηλότερο high και το χαμηλότερο low των τελευταίων N κεριών.
    # Είναι η βάση των Turtle Trading Rules. Breakout πάνω από το upper channel
    # = νέο N-period high = ισχυρό σήμα αγοράς.
    if strategy_cfg.get("DONCHIAN", {}).get("enabled"):
        try:
            # Επιστρέφει: [DCL (lower), DCM (middle), DCU (upper)]
            dc = df.ta.donchian(length=strategy_cfg["DONCHIAN"].get("period", 20), append=True)
            df['DC_signal'] = 0
            if dc is not None and not dc.empty:
                upper = dc[dc.columns[2]]  # Upper channel = N-period high
                # Μόνο breakout για Long: τιμή >= N-period high → ισχυρό σήμα αγοράς
                df.loc[df['close'] >= upper, 'DC_signal'] = 1 # Μόνο breakout για Long

            _s = df['DC_signal'].iloc[-1]
            logger.debug(f"[DONCHIAN] period={strategy_cfg['DONCHIAN'].get('period', 20)} | Αποτέλεσμα: {'BUY ✅' if _s == 1 else 'SELL 🔴' if _s == -1 else 'NEUTRAL ⚪'}")
        except Exception: pass

    # ==========================================
    # 4. VOLUME
    # ==========================================
    # Δείκτες που αναλύουν τον ΟΓΚΟ συναλλαγών για επιβεβαίωση τάσεων.
    # Βασική αρχή: τάση + υψηλός όγκος = αξιόπιστο σήμα.

    # ------------------------------------------
    # OBV - On Balance Volume
    # ------------------------------------------
    # Σωρευτικός δείκτης όγκου:
    #   Αν close > close_χθες → προσθέτει τον σημερινό όγκο στο OBV
    #   Αν close < close_χθες → αφαιρεί τον σημερινό όγκο από το OBV
    # Ανοδικό OBV με ανοδική τιμή = επιβεβαίωση τάσης.
    # Καθοδικό OBV με ανοδική τιμή = απόκλιση (bearish divergence).
    if strategy_cfg.get("OBV", {}).get("enabled"):
        try:
            cfg = strategy_cfg["OBV"]
            obv = df.ta.obv(append=True)
            sig_type = cfg.get("signal", "sma_crossover")
            df['OBV_signal'] = 0

            if sig_type == "trend":
                # TREND: OBV ανεβαίνει από προηγούμενο κερί → bullish
                # OBV κατεβαίνει → bearish. Απλή σύγκριση με .shift(1)
                df.loc[obv > obv.shift(1), 'OBV_signal'] = 1
                df.loc[obv < obv.shift(1), 'OBV_signal'] = -1

            elif sig_type == "sma_crossover":
                # SMA CROSSOVER: Υπολογίζει κυλιόμενο μέσο (rolling mean) του OBV
                # Σήμα αγοράς όταν το OBV περνά ΠΑΝΩ από τον ΚΜΟ του (επιτάχυνση εισροής όγκου)
                obv_sma = obv.rolling(cfg.get("trend_period", 10)).mean()
                df.loc[(obv > obv_sma) & (obv.shift(1) <= obv_sma.shift(1)), 'OBV_signal'] = 1
                df.loc[(obv < obv_sma) & (obv.shift(1) >= obv_sma.shift(1)), 'OBV_signal'] = -1

            _s = df['OBV_signal'].iloc[-1]
            logger.debug(f"[OBV] signal_type={sig_type} | trend_period={cfg.get('trend_period', 10)} | Αποτέλεσμα: {'BUY ✅' if _s == 1 else 'SELL 🔴' if _s == -1 else 'NEUTRAL ⚪'}")
        except Exception: pass

    # ------------------------------------------
    # CMF - Chaikin Money Flow
    # ------------------------------------------
    # Μετρά την πίεση αγοράς/πώλησης συνδυάζοντας τιμή και όγκο σε εύρος [-1, +1].
    # CMF > 0 = εισροή χρήματος (bullish), CMF < 0 = εκροή (bearish).
    # threshold: ελάχιστη τιμή CMF για να δοθεί σήμα (φιλτράρει αδύναμα σήματα).
    if strategy_cfg.get("CMF", {}).get("enabled"):
        try:
            cfg = strategy_cfg["CMF"]
            cmf = df.ta.cmf(length=cfg.get("period", 20), append=True)
            thresh = cfg.get("threshold", 0)  # Default 0 = οποιαδήποτε θετική/αρνητική τιμή

            df['CMF_signal'] = 0
            # CMF > thresh → θετική πίεση αγοράς → αγορά
            # CMF < -thresh → αρνητική πίεση (εκροή) → πώληση
            df.loc[cmf > thresh, 'CMF_signal'] = 1
            df.loc[cmf < -thresh, 'CMF_signal'] = -1

            _s = df['CMF_signal'].iloc[-1]
            _cmf_val = round(cmf.iloc[-1], 4) if cmf is not None else "N/A"
            logger.debug(f"[CMF] period={cfg.get('period', 20)} | threshold={thresh} | CMF={_cmf_val} | Αποτέλεσμα: {'BUY ✅' if _s == 1 else 'SELL 🔴' if _s == -1 else 'NEUTRAL ⚪'}")
        except Exception: pass

    # ------------------------------------------
    # ADX - Average Directional Index
    # ------------------------------------------
    # Μετρά τη ΔΥΝΑΜΗ (όχι κατεύθυνση) της τάσης σε εύρος 0-100.
    # ADX > 25 = ισχυρή τάση, ADX < 20 = sideways/ranging αγορά.
    # Αποτελείται από:
    #   ADX line : δύναμη τάσης (δεν λέει αν ανοδική ή καθοδική)
    #   +DI line : δύναμη bullish κίνησης
    #   -DI line : δύναμη bearish κίνησης
    if strategy_cfg.get("ADX", {}).get("enabled"):
        try:
            cfg = strategy_cfg["ADX"]
            adx_df = df.ta.adx(length=cfg.get("period", 14), append=True)
            thresh = cfg.get("threshold", 25)  # Κατώφλι δύναμης τάσης

            df['ADX_signal'] = 0
            if adx_df is not None and not adx_df.empty:
                # Παίρνει δυναμικά τις 3 στήλες: ADX, +DI, -DI
                adx_line, pos_di, neg_di = adx_df[adx_df.columns[0]], adx_df[adx_df.columns[1]], adx_df[adx_df.columns[2]]

                # Δίνει σήμα μόνο αν το ADX δείχνει ισχυρή τάση ΚΑΙ το +DI είναι πάνω από το -DI
                # Δηλαδή: "υπάρχει τάση" AND "η τάση είναι bullish" → αγορά
                # Αντίστοιχα: "υπάρχει τάση" AND "η τάση είναι bearish" → πώληση
                # Αν ADX < thresh (sideways) → κανένα σήμα (παραμένει 0)
                df.loc[(adx_line > thresh) & (pos_di > neg_di), 'ADX_signal'] = 1
                df.loc[(adx_line > thresh) & (pos_di < neg_di), 'ADX_signal'] = -1

                _s = df['ADX_signal'].iloc[-1]
                _adx_val = round(adx_line.iloc[-1], 2)
                _pdi_val = round(pos_di.iloc[-1], 2)
                _ndi_val = round(neg_di.iloc[-1], 2)
                logger.debug(f"[ADX] period={cfg.get('period', 14)} | threshold={thresh} | ADX={_adx_val} | +DI={_pdi_val} | -DI={_ndi_val} | Αποτέλεσμα: {'BUY ✅' if _s == 1 else 'SELL 🔴' if _s == -1 else 'NEUTRAL ⚪'}")
        except Exception: pass

    # =============================================================================
    # ΕΠΙΣΤΡΟΦΗ DataFrame
    # =============================================================================
    # Επιστρέφει το df εμπλουτισμένο με όλες τις στήλες δεικτών και signals.
    # Ο strategy_manager θα διαβάσει τις στήλες *_signal για να αποφασίσει
    # αν και πότε να μπει/βγει από θέση βάσει της επιλεγμένης λογικής (OR/MAJORITY/AND).
    return df
