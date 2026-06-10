import os
import pandas as pd
import pandas_ta as ta
from datetime import datetime, timedelta
import config
from utils.logger import logger

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
                    return trend_dict
            except Exception as e:
                logger.warning(f"⚠️ Σφάλμα ανάγνωσης trend cache: {e}")

        # Δεν υπάρχει cache – υπολογίζουμε από την αρχή
        logger.info(f"⏳ Υπολογισμός BTC trend για {padded_start_str} - {end_date} (1d & 4h)")

        # Λήψη δεδομένων 1d (αρκετά για EMA200)
        df_daily = self.exchange.get_candles_cached(btc_symbol, "1d", padded_start_str, end_date)
        if df_daily is None or df_daily.empty:
            logger.warning("⚠️ Αποτυχία λήψης 1d δεδομένων BTC. Trend filter disabled.")
            return {}

        # Λήψη δεδομένων 4h (για λεπτομερή σήματα)
        df_4h = self.exchange.get_candles_cached(btc_symbol, "4h", padded_start_str, end_date)
        if df_4h is None or df_4h.empty:
            logger.warning("⚠️ Αποτυχία λήψης 4h δεδομένων BTC. Trend filter disabled.")
            return {}

        # Υπολογισμός δεικτών για daily (EMA200, EMA50, Donchian)
        if len(df_daily) >= 200:
            df_daily['ema50'] = ta.ema(df_daily['close'], length=50).fillna(0)
            df_daily['ema200'] = ta.ema(df_daily['close'], length=200).fillna(0)

            # Μόνο αν η EMA200 έχει πραγματική τιμή (>0) κάνουμε τη σύγκριση
            df_daily['golden_cross'] = (df_daily['ema50'] > df_daily['ema200']) & (df_daily['ema200'] > 0)
            df_daily['above_emas'] = (df_daily['close'] > df_daily['ema50']) & (
                        df_daily['close'] > df_daily['ema200']) & (df_daily['ema200'] > 0)
        else:
            df_daily['golden_cross'] = False
            df_daily['above_emas'] = False

        # Donchian (20 διάστημα)
        if len(df_daily) >= 20:
            donchian = ta.donchian(df_daily['high'], df_daily['low'], lower_length=20, upper_length=20)
            if donchian is not None:
                df_daily['dc_mid'] = donchian.iloc[:, 1]
                df_daily['structure_bullish'] = df_daily['close'] > df_daily['dc_mid']
            else:
                df_daily['structure_bullish'] = True
        else:
            df_daily['structure_bullish'] = True

        # Συνολικό daily trend (bullish αν όλα αληθή)
        if len(df_daily) >= 200:
            df_daily['daily_bullish'] = df_daily['golden_cross'] & df_daily['above_emas'] & df_daily['structure_bullish']
        else:
            df_daily['daily_bullish'] = df_daily['structure_bullish']

        # Υπολογισμός 4h trend: απλό (π.χ. price > EMA50 και EMA50 > EMA200) – μπορείς να το προσαρμόσεις
        if len(df_4h) >= 200:
            df_4h['ema50'] = ta.ema(df_4h['close'], length=50).fillna(0)
            df_4h['ema200'] = ta.ema(df_4h['close'], length=200).fillna(0)
            df_4h['4h_bullish'] = (df_4h['close'] > df_4h['ema50']) & (df_4h['ema50'] > df_4h['ema200']).fillna(0)
        else:
            # Αν δεν έχουμε αρκετά δεδομένα, θεωρούμε ότι δεν είναι bullish (ασφαλές)
            df_4h['4h_bullish'] = False

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

        # Δημιουργία dict (timestamp -> bool)
        trend_dict = dict(zip(df_4h['timestamp'], df_4h['btc_uptrend']))

        # Αποθήκευση cache
        cache_df = pd.DataFrame(list(trend_dict.items()), columns=['timestamp', 'btc_uptrend'])
        cache_df.to_parquet(cache_path, index=False)
        logger.info(f"💾 [TREND CACHE SAVE] Αποθηκεύτηκε BTC trend ({len(trend_dict)} σημεία) στο {cache_path}")

        return trend_dict