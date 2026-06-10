import logging
import os
import atexit
from datetime import datetime
from logging.handlers import RotatingFileHandler
import config

# 1. Δημιουργία του φακέλου Logs αν δεν υπάρχει
config.LOGS_DIR.mkdir(parents=True, exist_ok=True)

# 2. Παραγωγή του αρχικού ονόματος (Μόνο ώρα έναρξης)
start_time_str = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
current_log_file = config.LOGS_DIR / f"bot_run_{start_time_str}_ACTIVE.log"

logger = logging.getLogger("TradingBotOS")
logger.setLevel(logging.DEBUG)
formatter = logging.Formatter('%(asctime)s - %(levelname)s - %(message)s')

# Handler για το Τερματικό (Console)
console_handler = logging.StreamHandler()
console_handler.setFormatter(formatter)
logger.addHandler(console_handler)

# Handler για το Αρχείο (Αλλάζει αρχείο αν ξεπεράσει τα 2MB)
file_handler = RotatingFileHandler(
    filename=current_log_file, 
    maxBytes=config.LOG_MAX_BYTES,           # 2 Megabytes
    backupCount=config.LOG_BACKUP_COUNT,     # Κρατάει τα 5 πιο πρόσφατα
    encoding='utf-8'
)
file_handler.setFormatter(formatter)
logger.addHandler(file_handler)

def change_log_level(level_str):
    levels = {"INFO": logging.INFO, "DEBUG": logging.DEBUG, "WARNING": logging.WARNING, "ERROR": logging.ERROR}
    level = levels.get(level_str.upper(), logging.INFO)
    logger.setLevel(level)
    for handler in logger.handlers:
        handler.setLevel(level)

# 3. ΜΕΤΟΝΟΜΑΣΙΑ ΚΑΤΑ ΤΟ ΚΛΕΙΣΙΜΟ:
# Όταν κλείνει το Python script, τρέχει αυτόματα αυτή η συνάρτηση
def rename_log_on_exit():
    end_time_str = datetime.now().strftime("%H-%M-%S")
    final_log_name = config.LOGS_DIR / f"bot_run_{start_time_str}_to_{end_time_str}.log"
    
    # Κλείνουμε τα αρχεία για να επιτραπεί η μετονομασία στα Windows
    for handler in logger.handlers:
        handler.close()
        
    try:
        os.rename(current_log_file, final_log_name)
    except Exception:
        pass

atexit.register(rename_log_on_exit)