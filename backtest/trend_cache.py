import os
import pandas as pd
import pandas_ta as ta
from datetime import datetime, timedelta
import config
from utils.logger import logger


# =============================================================================
# Κοινή λογική υπολογισμού "BTC Trend" (Multi-Timeframe: 1d + 4h)
# =============================================================================
# Όλες οι ρυθμίσεις (EMA periods, Donchian period) έρχονται ΑΠΟΚΛΕΙΣΤΙΚΑ από
# config.BTC_TREND_CONFIG (κεντρικός εγκέφαλος) - τίποτα hardcoded εδώ.
#
# Αυτές οι 2 συναρτήσεις χρησιμοποιούνται ΚΑΙ από το get_btc_trend_filter()
# (ιστορικό, για το Backtest/Screener) ΚΑΙ από το get_current_btc_trend()
# (live, για το auto_trade_loop) - ώστε η λογική να είναι ΠΑΝΤΑ η ίδια.
# =============================================================================

def _compute_daily_bullish(df_daily: pd.DataFrame) -> pd.DataFrame:
    """
    Υπολογίζει τη στήλη 'daily_bullish' στο df_daily (1d candles):
      - golden_cross: EMA_fast > EMA_slow (και EMA_slow > 0)
      - above_emas: τιμή πάνω από EMA_fast ΚΑΙ EMA_slow
      - structure_bullish: τιμή πάνω από το Donchian mid (Donchian_period)
      - daily_bullish = golden_cross & above_emas & structure_bullish
        (αν δεν υπάρχουν αρκετά candles για EMA_slow, daily_bullish = structure_bullish)
    """
    cfg = config.BTC_TREND_CONFIG
    ema_fast, ema_slow, donchian_period = cfg["ema_fast"], cfg["ema_slow"], cfg["donchian_period"]

    if len(df_daily) >= ema_slow:
        df_daily['ema_fast'] = ta.ema(df_daily['close'], length=ema_fast).fillna(0)
        df_daily['ema_slow'] = ta.ema(df_daily['close'], length=ema_slow).fillna(0)

        df_daily['golden_cross'] = (df_daily['ema_fast'] > df_daily['ema_slow']) & (df_daily['ema_slow'] > 0)
        df_daily['above_emas'] = (df_daily['close'] > df_daily['ema_fast']) & (
                    df_daily['close'] > df_daily['ema_slow']) & (df_daily['ema_slow'] > 0)
    else:
        df_daily['golden_cross'] = False
        df_daily['above_emas'] = False

    # Donchian (structure) - δείκτης δομής αγοράς
    if len(df_daily) >= donchian_period:
        donchian = ta.donchian(df_daily['high'], df_daily['low'],
                                lower_length=donchian_period, upper_length=donchian_period)
        if donchian is not None:
            df_daily['dc_mid'] = donchian.iloc[:, 1]
            df_daily['structure_bullish'] = df_daily['close'] > df_daily['dc_mid']
        else:
            df_daily['structure_bullish'] = True
    else:
        df_daily['structure_bullish'] = True

    # Συνολικό daily trend (bullish αν όλα αληθή)
    if len(df_daily) >= ema_slow:
        df_daily['daily_bullish'] = df_daily['golden_cross'] & df_daily['above_emas'] & df_daily['structure_bullish']
    else:
        df_daily['daily_bullish'] = df_daily['structure_bullish']

    return df_daily


def _compute_4h_bullish(df_4h: pd.DataFrame) -> pd.DataFrame:
    """
    Υπολογίζει τη στήλη '4h_bullish' στο df_4h (4h candles):
      4h_bullish = τιμή > EMA_fast > EMA_slow
    (αν δεν υπάρχουν αρκετά candles για EMA_slow, 4h_bullish = False -> ασφαλές)
    """
    cfg = config.BTC_TREND_CONFIG
    ema_fast, ema_slow = cfg["ema_fast"], cfg["ema_slow"]

    if len(df_4h) >= ema_slow:
        df_4h['ema_fast'] = ta.ema(df_4h['close'], length=ema_fast).fillna(0)
        df_4h['ema_slow'] = ta.ema(df_4h['close'], length=ema_slow).fillna(0)
        df_4h['4h_bullish'] = (df_4h['close'] > df_4h['ema_fast']) & (df_4h['ema_fast'] > df_4h['ema_slow']).fillna(0)
    else:
        df_4h['4h_bullish'] = False

    return df_4h


def _log_trend_periods(df_4h: pd.DataFrame, start_date: str, end_date: str) -> None:
    """
    DEBUG: Καταγράφει στα logs αν η ζητούμενη περίοδος [start_date, end_date]
    ήταν εξ ολοκλήρου UPTREND/DOWNTREND, ή -αν αλλάζει- τα διαστήματα
    (από-έως) κάθε φάσης UPTREND/DOWNTREND.
    """
    # Τα timestamps του df_4h μπορεί να είναι tz-aware (UTC) ή tz-naive, ανάλογα
    # με την πηγή (cache vs νέο κατέβασμα) - ευθυγραμμίζουμε το start/end_date
    # με το tz του df_4h πριν τη σύγκριση, ώστε να μην σκάει το pandas.
    start_ts = pd.to_datetime(start_date)
    end_ts = pd.to_datetime(end_date)
    if df_4h['timestamp'].dt.tz is not None:
        start_ts = start_ts.tz_localize(df_4h['timestamp'].dt.tz)
        end_ts = end_ts.tz_localize(df_4h['timestamp'].dt.tz)

    mask = (df_4h['timestamp'] >= start_ts) & (df_4h['timestamp'] <= end_ts)
    df_period = df_4h.loc[mask, ['timestamp', 'btc_uptrend']].reset_index(drop=True)

    if df_period.empty:
        logger.debug(f"📈 [BTC TREND] Δεν υπάρχουν δεδομένα BTC trend για την περίοδο {start_date} -> {end_date}")
        return

    if df_period['btc_uptrend'].nunique() == 1:
        state = "UPTREND 🟢" if bool(df_period['btc_uptrend'].iloc[0]) else "DOWNTREND 🔴"
        logger.debug(f"📈 [BTC TREND] Όλη η περίοδος {start_date} -> {end_date} είναι {state}")
        return

    logger.debug(f"📈 [BTC TREND] Ανάλυση περιόδου {start_date} -> {end_date}:")
    block_start_ts = df_period['timestamp'].iloc[0]
    block_state = bool(df_period['btc_uptrend'].iloc[0])

    for i in range(1, len(df_period)):
        current_state = bool(df_period['btc_uptrend'].iloc[i])
        if current_state != block_state:
            label = "UPTREND 🟢" if block_state else "DOWNTREND 🔴"
            logger.debug(f"   {label}: {block_start_ts} -> {df_period['timestamp'].iloc[i - 1]}")
            block_start_ts = df_period['timestamp'].iloc[i]
            block_state = current_state

    label = "UPTREND 🟢" if block_state else "DOWNTREND 🔴"
    logger.debug(f"   {label}: {block_start_ts} -> {df_period['timestamp'].iloc[-1]}")


class TrendCache:
    def __init__(self, exchange):
        self.exchange = exchange
        self.cache_dir = config.TREND_CACHE_DIR
        os.makedirs(self.cache_dir, exist_ok=True)

    def get_btc_trend_filter(self, start_date: str, end_date: str, base_currency: str = None) -> dict:
        """
        Επιστρέφει dict {timestamp: bool} όπου True σημαίνει ότι και τα 2 timeframes (1d, 4h) είναι bullish.
        - start_date, end_date: strings "YYYY-MM-DD"
        - base_currency: π.χ. "USDC" (default config.BASE_CURRENCY)

        DEBUG: Καταγράφει επίσης στα logs αν η ζητούμενη περίοδος [start_date,
        end_date] ήταν UPTREND/DOWNTREND συνολικά, ή τα διαστήματα κάθε φάσης.
        """
        base = base_currency or config.BASE_CURRENCY
        btc_symbol = f"BTC{base}"

        # Χρειαζόμαστε padding για υπολογισμό δεικτών: +180 ημέρες πριν το start_date
        start_dt = datetime.strptime(start_date, "%Y-%m-%d")
        padded_start = start_dt - timedelta(days=180)
        padded_start_str = padded_start.strftime("%Y-%m-%d")

        # Δημιουργία cache filename που περιλαμβάνει και τα δύο timeframes
        cache_filename = f"{btc_symbol}_trend_{padded_start_str}_{end_date}.parquet"
        cache_path = self.cache_dir / cache_filename

        if cache_path.exists():
            try:
                df_cache = pd.read_parquet(cache_path)
                if not df_cache.empty:
                    trend_dict = dict(zip(df_cache['timestamp'], df_cache['btc_uptrend']))
                    logger.debug(f"✅ [TREND CACHE HIT] Φόρτωση BTC trend από {cache_path}")

                    # DEBUG: log των διαστημάτων UPTREND/DOWNTREND για τη ζητούμενη περίοδο
                    _log_trend_periods(df_cache, start_date, end_date)
                    return trend_dict
            except Exception as e:
                logger.warning(f"⚠️ Σφάλμα ανάγνωσης trend cache: {e}")

        # Δεν υπάρχει cache – υπολογίζουμε από την αρχή
        logger.info(f"⏳ Υπολογισμός BTC trend για {padded_start_str} - {end_date} (1d & 4h)")

        # Λήψη δεδομένων 1d (αρκετά για EMA_slow)
        df_daily = self.exchange.get_candles_cached(btc_symbol, "1d", padded_start_str, end_date)
        if df_daily is None or df_daily.empty:
            logger.warning("⚠️ Αποτυχία λήψης 1d δεδομένων BTC. Trend filter disabled.")
            return {}

        # Λήψη δεδομένων 4h (για λεπτομερή σήματα)
        df_4h = self.exchange.get_candles_cached(btc_symbol, "4h", padded_start_str, end_date)
        if df_4h is None or df_4h.empty:
            logger.warning("⚠️ Αποτυχία λήψης 4h δεδομένων BTC. Trend filter disabled.")
            return {}

        # Υπολογισμός δεικτών για 1d & 4h (κοινή λογική, βλ. config.BTC_TREND_CONFIG)
        df_daily = _compute_daily_bullish(df_daily)
        df_4h = _compute_4h_bullish(df_4h)

        # Συγχώνευση: Εξασφαλίζουμε ότι τα dates είναι καθαρά (χωρίς ώρες)
        df_4h['date_only'] = df_4h['timestamp'].dt.floor('D')
        df_daily['date_only'] = df_daily['timestamp'].dt.floor('D')

        # Δημιουργία Λεξικού από το Daily για γρήγορο Map
        daily_bullish_map = df_daily.set_index('date_only')['daily_bullish'].to_dict()

        # Εφαρμογή (Map) στο 4h DataFrame
        df_4h['daily_bullish'] = df_4h['date_only'].map(daily_bullish_map).fillna(False)
        df_4h['daily_bullish'] = df_4h['daily_bullish'].fillna(False)

        # Τελικό trend: και τα δύο bullish
        df_4h['btc_uptrend'] = df_4h['daily_bullish'] & df_4h['4h_bullish']

        # DEBUG: log των διαστημάτων UPTREND/DOWNTREND για τη ζητούμενη περίοδο
        _log_trend_periods(df_4h, start_date, end_date)

        # Δημιουργία dict (timestamp -> bool)
        trend_dict = dict(zip(df_4h['timestamp'], df_4h['btc_uptrend']))

        # Αποθήκευση cache
        cache_df = df_4h[['timestamp', 'btc_uptrend']]
        cache_df.to_parquet(cache_path, index=False)
        logger.info(f"💾 [TREND CACHE SAVE] Αποθηκεύτηκε BTC trend ({len(trend_dict)} σημεία) στο {cache_path}")

        return trend_dict

    def get_current_btc_trend(self, base_currency: str = None) -> dict:
        """
        Υπολογίζει την ΤΡΕΧΟΥΣΑ τάση του BTC (UPTREND/DOWNTREND) με βάση τα πιο
        πρόσφατα 1d & 4h candles, χρησιμοποιώντας την ΙΔΙΑ λογική με το
        get_btc_trend_filter() (config.BTC_TREND_CONFIG).

        Χρησιμοποιείται από το auto_trade_loop (app.py) για DEBUG logging πριν
        από κάθε έλεγχο ανοίγματος θέσης - ΔΕΝ μπλοκάρει αγορές, είναι μόνο
        ενημερωτικό.

        Επιστρέφει dict:
          {"uptrend": bool|None, "daily_bullish": bool|None, "4h_bullish": bool|None}
        (None αν δεν ήταν δυνατή η λήψη δεδομένων).
        """
        base = base_currency or config.BASE_CURRENCY
        btc_symbol = f"BTC{base}"
        ema_slow = config.BTC_TREND_CONFIG["ema_slow"]

        now = datetime.now()
        end_date = now.strftime("%Y-%m-%d")

        # 1d: χρειαζόμαστε >= ema_slow ημέρες (+ buffer) για να βγει σωστή EMA_slow
        daily_start = (now - timedelta(days=ema_slow + 30)).strftime("%Y-%m-%d")
        # 4h: χρειαζόμαστε >= ema_slow candles * 4 ώρες (+ buffer ημερών)
        h4_start = (now - timedelta(days=(ema_slow * 4 / 24) + 10)).strftime("%Y-%m-%d")

        df_daily = self.exchange.get_candles_cached(btc_symbol, "1d", daily_start, end_date)
        df_4h = self.exchange.get_candles_cached(btc_symbol, "4h", h4_start, end_date)

        if df_daily is None or df_daily.empty or df_4h is None or df_4h.empty:
            logger.warning("⚠️ [BTC TREND] Αποτυχία λήψης δεδομένων BTC για live trend check.")
            return {"uptrend": None, "daily_bullish": None, "4h_bullish": None}

        df_daily = df_daily.sort_values('timestamp').reset_index(drop=True)
        df_4h = df_4h.sort_values('timestamp').reset_index(drop=True)

        df_daily = _compute_daily_bullish(df_daily)
        df_4h = _compute_4h_bullish(df_4h)

        daily_bullish_now = bool(df_daily['daily_bullish'].iloc[-1])
        h4_bullish_now = bool(df_4h['4h_bullish'].iloc[-1])

        return {
            "uptrend": daily_bullish_now and h4_bullish_now,
            "daily_bullish": daily_bullish_now,
            "4h_bullish": h4_bullish_now
        }
