import time
import hmac
import hashlib
import requests
import pandas as pd
from urllib.parse import urlencode
from utils.logger import logger
import config
from datetime import datetime, timezone
import os
from pathlib import Path


class BinanceExchange:
    def __init__(self):
        self.api_key = config.BINANCE_API_KEY
        self.api_secret = config.BINANCE_API_SECRET
        self.base_url = config.BINANCE_BASE_URL
        self.headers = {'X-MBX-APIKEY': self.api_key} if self.api_key else {}

        # === ΜΕΤΑΒΛΗΤΕΣ ΓΙΑ CACHING ΩΡΑΣ ===
        self._time_offset = 0
        self._last_time_sync = 0  # Timestamp τελευταίου συγχρονισμού

        logger.debug(
            "🔧 BinanceExchange: Αρχικοποιήθηκε (API Key: {}...)".format(self.api_key[:5] if self.api_key else "None"))

    def _generate_signature(self, data: dict) -> str:
        if not self.api_secret: 
            logger.error("❌ Λείπει το BINANCE_API_SECRET. Αδύνατη η δημιουργία υπογραφής.")
            raise ValueError("Λείπει το BINANCE_API_SECRET.")
        return hmac.new(self.api_secret.encode(), urlencode(data).encode(), hashlib.sha256).hexdigest()

    def _make_request(self, method: str, endpoint: str, data: dict = None, signed: bool = False):
        if data is None:
            data = {}

        if signed:
            # Καλεί τη νέα get_server_time που διαχειρίζεται ΚΑΙ το offset
            self.get_server_time()
            data['timestamp'] = int(time.time() * 1000) + self._time_offset
            data['signature'] = self._generate_signature(data)

        url = self.base_url + endpoint

        try:
            if method.upper() == "GET":
                res = requests.get(url, headers=self.headers, params=data if data else None, timeout=15)
            else:
                res = requests.post(url, headers=self.headers, data=data if data else None, timeout=15)

            if res.status_code == 200:
                return res.json()
            else:
                logger.error(f"❌ Binance API Error ({res.status_code}): {res.text}")
                return None
        except Exception as e:
            logger.error(f"❌ Σφάλμα κατά την εκτέλεση του request στο endpoint {endpoint}: {e}")
            return None

    def get_server_time(self):
        """Επιστρέφει την ώρα της Binance (timezone aware) και ανανεώνει το time_offset αν χρειάζεται (caching 1 ώρα)."""
        current_time = time.time()

        # Αν έχουν περάσει πάνω από 3600 δευτερόλεπτα (1 ώρα) ή είναι η πρώτη φορά, ζήτα την ώρα
        if current_time - self._last_time_sync > 3600 or self._last_time_sync == 0:
            logger.debug("⏱️ Ανάκτηση ώρας από Binance Server...")
            try:
                res = requests.get('https://api.binance.com/api/v3/time', timeout=5)
                if res.status_code == 200:
                    server_time_ms = res.json()['serverTime']

                    # Υπολογισμός και αποθήκευση του offset για τα επόμενα signed requests
                    self._time_offset = server_time_ms - int(current_time * 1000)
                    self._last_time_sync = current_time

                    dt = datetime.fromtimestamp(server_time_ms / 1000, tz=timezone.utc)
                    logger.debug(f"⏱️ Binance Server Time Συγχρονίστηκε: {dt} (Offset: {self._time_offset}ms)")
                    return dt
            except Exception as e:
                logger.error(f"❌ Σφάλμα σύνδεσης με Binance Time API: {e}")

        # Αν χρησιμοποιούμε cached time, υπολογίζουμε το τρέχον datetime προσθέτοντας το offset
        estimated_server_time_ms = int(time.time() * 1000) + self._time_offset
        return datetime.fromtimestamp(estimated_server_time_ms / 1000, tz=timezone.utc)

    def get_top_volume_pairs(self, limit: int, quote_asset: str, trading_type: str = "spot") -> list:
        """
        Επιστρέφει τα κορυφαία σε όγκο ζεύγη βάσει του quote_asset (Base Currency από το UI)
        και φιλτράρει ανάλογα με το αν είμαστε σε 'spot' ή 'margin' αγορά.
        """
        try:
            # 1. Ανάκτηση πληροφοριών αγοράς από τη Binance
            url_info = f"{self.base_url}/api/v3/exchangeInfo"
            response_info = requests.get(url_info)
            if response_info.status_code != 200:
                logger.error("❌ Αδυναμία ανάκτησης exchangeInfo από τη Binance.")
                return []

            data_info = response_info.json()

            # Καθορισμός αν είμαστε σε Margin Mode
            is_margin = (trading_type.lower() == "margin")
            market_label = "MARGIN" if is_margin else "SPOT"

            # Φιλτράρισμα ζευγών με βάση το Base Currency (quoteAsset) και τα επίσημα booleans της Binance
            allowed_symbols = set()
            for s in data_info.get('symbols', []):
                if s['quoteAsset'].upper() == quote_asset.upper() and s['status'] == 'TRADING':
                    # Χρήση των bulletproof boolean πεδίων της Binance
                    if is_margin and s.get('isMarginTradingAllowed', False):
                        allowed_symbols.add(s['symbol'])
                    elif not is_margin and s.get('isSpotTradingAllowed', False):
                        allowed_symbols.add(s['symbol'])

            # 2. Ανάκτηση των 24h Tickers για τον υπολογισμό του όγκου συναλλαγών
            url_ticker = f"{self.base_url}/api/v3/ticker/24hr"
            response_ticker = requests.get(url_ticker)
            if response_ticker.status_code != 200:
                logger.error("❌ Αδυναμία ανάκτησης ticker/24hr από τη Binance.")
                return []

            tickers = response_ticker.json()

            # Κρατάμε μόνο τα ζεύγη που πέρασαν το φιλτράρισμα της αγοράς
            pairs_with_volume = []
            for t in tickers:
                symbol = t['symbol']
                if symbol in allowed_symbols:
                    pairs_with_volume.append({
                        'symbol': symbol,
                        'volume': float(t.get('quoteVolume', 0))
                    })

            # Ταξινόμηση με βάση τον όγκο (φθίνουσα σειρά)
            pairs_with_volume.sort(key=lambda x: x['volume'], reverse=True)

            # Επιστροφή των κορυφαίων ζευγών βάσει του limit
            top_pairs = [p['symbol'] for p in pairs_with_volume[:limit]]
            logger.info(
                f"📊 [SCREENER] Βρέθηκαν {len(top_pairs)} κορυφαία ζεύγη για την αγορά {market_label} με Base {quote_asset}.")
            return top_pairs

        except Exception as e:
            logger.error(f"❌ Σφάλμα κατά την ανάκτηση των κορυφαίων ζευγών: {e}")
            return []

    def get_historical_top_volume_pairs(self, start_time: int, end_time: int, limit: int = 5, pool_size: int = 25) -> list:
        logger.info(f"🔍 Αναζήτηση των top {limit} ζευγών βάσει ιστορικού όγκου...")
        current_top = self.get_top_volume_pairs(limit=pool_size)
        volumes = {}
        for sym in current_top:
            df = self.get_historical_data(sym, "1d", start_time=start_time, end_time=end_time)
            volumes[sym] = df['qav'].sum() if not df.empty and 'qav' in df.columns else 0
            time.sleep(config.SLEEP_API_DELAY)
        sorted_pairs = sorted(volumes.items(), key=lambda x: x[1], reverse=True)
        return [p[0] for p in sorted_pairs[:limit]]

    def get_historical_data(self, symbol: str, timeframe: str, start_time=None, end_time=None) -> pd.DataFrame:
        # 🛑 ΕΛΕΓΧΟΣ ΑΚΥΡΩΣΗΣ
        if config.BACKTEST_CONFIG.get('cancel', False):
            logger.warning(
                f"🛑 Το Backtest ακυρώθηκε (BACKTEST_CONFIG['cancel'] == True). Διακοπή λήψης για το {symbol}...")
            raise RuntimeError("Backtest Canceled by User")

        """Λήψη ιστορικών δεδομένων απευθείας από το API (χωρίς cache). Χρήσιμο για εσωτερικές κλήσεις (π.χ. screener όγκου)"""
        logger.debug(f"📉 [RAW DOWNLOAD] {symbol} {timeframe} (Απευθείας λήψη χωρίς Cache)")

        # --- ΠΡΟΣΘΗΚΗ: Έξυπνη μετατροπή χρόνου από "YYYY-MM-DD" σε Binance Timestamps (ms) ---
        def to_binance_ms(time_input):
            if not time_input: return None
            # Αν είναι ήδη float ή int (π.χ. timestamp), απλά επέστρεψε το ως int
            if isinstance(time_input, (int, float)):
                return int(time_input)
            # Αν είναι string (π.χ. από το form '2025-11-02')
            if isinstance(time_input, str):
                try:
                    dt = pd.to_datetime(time_input)
                    if dt.tz is None: dt = dt.tz_localize('UTC')
                    return int(dt.timestamp() * 1000)
                except Exception:
                    return None
            return None

        # Μετατρέπουμε τα start/end πριν ξεκινήσει το loop
        current_start = to_binance_ms(start_time)
        end_time_ms = to_binance_ms(end_time)

        all_candles = []

        while True:
            data = {'symbol': symbol, 'interval': timeframe, 'limit': config.KLINES_LIMIT}

            # Τώρα είναι 100% ασφαλές να μπούνε ως καθαροί ακέραιοι
            if current_start: data['startTime'] = int(current_start)
            if end_time_ms: data['endTime'] = int(end_time_ms)

            raw_candles = self._make_request("GET", "/api/v3/klines", data, signed=False)
            if not raw_candles: break

            all_candles.extend(raw_candles)

            if start_time is None: break
            if len(raw_candles) < 1000: break

            current_start = raw_candles[-1][0] + 1
            if end_time_ms and current_start >= end_time_ms: break
            time.sleep(config.SLEEP_API_DELAY)

        if not all_candles:
            logger.debug(f"⚠️ [RAW DOWNLOAD] Δεν βρέθηκαν κεριά για {symbol}")
            return pd.DataFrame()

        df = pd.DataFrame(all_candles,
                          columns=['timestamp', 'open', 'high', 'low', 'close', 'volume', 'close_time', 'qav',
                                   'num_trades', 'tbb', 'tbq', 'ignore'])
        df.drop_duplicates(subset=['timestamp'], inplace=True)
        df['timestamp'] = pd.to_datetime(df['timestamp'], unit='ms', utc=True)
        for col in ['open', 'high', 'low', 'close', 'volume', 'qav']: df[col] = df[col].astype(float)
        logger.debug(f"✅ [RAW DOWNLOAD] {symbol} κατέβασε {len(df)} κεριά.")
        return df

    def get_candles_cached(self, symbol: str, timeframe: str, start_date_str, end_date_str) -> pd.DataFrame:
        """Έξυπνη ανάκτηση κεριών με χρήση εξωτερικού Parquet Cache."""
        base_dir = Path(config.HISTORICAL_DATA_DIR)

        # Timeframes \ Base Currency \ Timeframe (π.χ. Timeframes\USDC\15m)
        # Χρησιμοποιούμε το config.BASE_CURRENCY που ενημερώνεται δυναμικά από το UI
        folder_path = base_dir / config.BASE_CURRENCY / timeframe
        file_path = folder_path / f"{symbol}.parquet"

        folder_path.mkdir(parents=True, exist_ok=True)
        
        req_start = pd.to_datetime(start_date_str).tz_localize('UTC') if pd.to_datetime(start_date_str).tz is None else pd.to_datetime(start_date_str).tz_convert('UTC')
        req_end = pd.to_datetime(end_date_str).tz_localize('UTC') if pd.to_datetime(end_date_str).tz is None else pd.to_datetime(end_date_str).tz_convert('UTC')

        logger.debug(f"🗂️ [CACHE] Ζητήθηκαν δεδομένα {symbol} {timeframe} από {req_start.date()} έως {req_end.date()}")

        df_cached = pd.DataFrame()

        if file_path.exists():
            try:
                df_cached = pd.read_parquet(file_path)
                if not df_cached.empty:
                    df_cached['timestamp'] = pd.to_datetime(df_cached['timestamp'])
                    if df_cached['timestamp'].dt.tz is None:
                        df_cached['timestamp'] = df_cached['timestamp'].dt.tz_localize('UTC')
                    else:
                        df_cached['timestamp'] = df_cached['timestamp'].dt.tz_convert('UTC')
                logger.debug(f"🗂️ [CACHE HIT] Διαβάστηκαν {len(df_cached)} κεριά από {file_path}")
            except Exception as e:
                logger.warning(f"⚠️ Σφάλμα ανάγνωσης αρχείου Cache για {symbol}, θα αναδημιουργηθεί: {e}")
                df_cached = pd.DataFrame()
        else:
            logger.debug(f"🗂️ [CACHE MISS] Δεν υπάρχει αρχείο parquet για {symbol} στο {file_path}")

        dfs_to_combine = []
        if not df_cached.empty:
            dfs_to_combine.append(df_cached)
            cache_start = df_cached['timestamp'].min()
            cache_end = df_cached['timestamp'].max()

            if req_start < cache_start:
                logger.info(f"📥 [CACHE GAP PAST] {symbol}: Λήψη παλαιότερων δεδομένων (από {req_start.date()} έως {cache_start.date()})")
                df_past = self._download_raw_klines(symbol, timeframe, req_start, cache_start)
                if not df_past.empty: dfs_to_combine.append(df_past)

            if req_end > cache_end:
                logger.info(f"📥 [CACHE GAP FUTURE] {symbol}: Λήψη νεότερων δεδομένων (από {cache_end.date()} έως {req_end.date()})")
                df_future = self._download_raw_klines(symbol, timeframe, cache_end, req_end)
                if not df_future.empty: dfs_to_combine.append(df_future)
        else:
            logger.info(f"📥 [FULL DOWNLOAD] Κατέβασμα πλήρους εύρους για {symbol}...")
            df_all = self._download_raw_klines(symbol, timeframe, req_start, req_end)
            if not df_all.empty: dfs_to_combine.append(df_all)

        if dfs_to_combine:
            final_df = pd.concat(dfs_to_combine, ignore_index=True)
            final_df.drop_duplicates(subset=['timestamp'], keep='last', inplace=True)
            final_df.sort_values('timestamp', inplace=True)
            final_df.reset_index(drop=True, inplace=True)

            try:
                final_df.to_parquet(file_path, engine='pyarrow', index=False)
                logger.debug(f"💾 [CACHE SAVE] Ενημερώθηκε το parquet για {symbol}. Σύνολο κεριών: {len(final_df)}")
            except Exception as e:
                logger.error(f"❌ Αποτυχία εγγραφής Parquet στο δίσκο για {symbol}: {e}")

            mask = (final_df['timestamp'] >= req_start) & (final_df['timestamp'] <= req_end)
            final_result = final_df.loc[mask].copy()
            logger.debug(f"✅ [CACHE RETURN] Επιστρέφονται {len(final_result)} κεριά για το φιλτραρισμένο διάστημα.")
            return final_result

        return pd.DataFrame()

    def _download_raw_klines(self, symbol: str, timeframe: str, start_dt: datetime, end_dt: datetime) -> pd.DataFrame:
        """Η πραγματική μηχανή pagination."""
        start_time_ms = int(start_dt.timestamp() * 1000)
        end_time_ms = int(end_dt.timestamp() * 1000)
        
        all_candles = []
        current_start = start_time_ms
        chunk_count = 1

        logger.debug(f"🔄 Ξεκινάει Pagination για {symbol} ({chunk_count}ο request)...")

        while True:
            # ΠΡΟΣΘΗΚΗ int() στο current_start και end_time_ms
            data = {'symbol': symbol, 'interval': timeframe, 'limit': 1000, 'startTime': int(current_start),
                    'endTime': int(end_time_ms)}
            
            raw_candles = self._make_request("GET", "/api/v3/klines", data, signed=False)
            if not raw_candles: break

            all_candles.extend(raw_candles)
            logger.debug(f"🔄 {symbol}: Κατέβηκαν {len(raw_candles)} κεριά στο {chunk_count}ο request.")

            if len(raw_candles) < 1000: break

            current_start = raw_candles[-1][0] + 1
            if current_start >= end_time_ms: break
            
            chunk_count += 1
            time.sleep(config.SLEEP_API_DELAY)

        if not all_candles: return pd.DataFrame()

        df = pd.DataFrame(all_candles, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume', 'close_time', 'qav', 'num_trades', 'tbb', 'tbq', 'ignore'])
        df.drop_duplicates(subset=['timestamp'], inplace=True)
        df['timestamp'] = pd.to_datetime(df['timestamp'], unit='ms', utc=True)
        for col in ['open', 'high', 'low', 'close', 'volume', 'qav']: df[col] = df[col].astype(float)
        
        logger.debug(f"✅ Ολοκληρώθηκε η λήψη {len(df)} νέων κεριών για {symbol}.")
        return df

    def get_open_positions(self) -> list:
        if config.BOT_MODE == "paper":
            logger.debug(f"💼 [PAPER BALANCE] Ανοιχτές θέσεις: {getattr(config, 'PAPER_OPEN_POSITIONS', [])}")
            return getattr(config, 'PAPER_OPEN_POSITIONS', [])

        balances = self._make_request("GET", "/api/v3/account", signed=True)
        if not balances: return []

        # Αντί για καρφωτό "USDC", πλέον ρωτάει το config ποιο είναι το ενεργό Base Currency!
        base_curr = config.BASE_CURRENCY
        positions = [asset['asset'] + base_curr for asset in balances.get('balances', []) if
                     float(asset['free']) > 0.001 and asset['asset'] != base_curr]

        logger.debug(f"💼 [LIVE BALANCE] Ανοιχτές θέσεις: {positions}")
        return positions

    def place_order(self, symbol: str, side: str, quantity: float, order_type: str = "MARKET"):
        logger.debug(f"🛒 [ORDER] Αίτημα τοποθέτησης: {side} {quantity} {symbol} ({order_type})")
        if config.BOT_MODE == "paper": 
            logger.debug("🛒 [ORDER PAPER] Η εντολή εκτελέστηκε εικονικά (SUCCESS).")
            return {"status": "SUCCESS"}
        
        data = {'symbol': symbol, 'side': side.upper(), 'type': order_type.upper(), 'quantity': quantity}
        res = self._make_request("POST", "/api/v3/order", data, signed=True)
        logger.debug(f"🛒 [ORDER LIVE] Αποτέλεσμα: {res}")
        return res

    def update_trailing_stop(self, symbol: str, new_stop_price: float):
        if config.BOT_MODE == "paper":
            logger.debug(f"🛡️ [PAPER] Trailing Stop ενημερώθηκε για {symbol} @ {new_stop_price:.4f}")
            return True
        else:
            logger.info(f"🛡️ [LIVE] Θα γινόταν update Stop Order για {symbol} @ {new_stop_price:.4f}")
            return True