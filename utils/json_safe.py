# =============================================================================
# json_safe.py
# =============================================================================
# Σκοπός: Μετατρέπει αναδρομικά (recursive) ένα οποιοδήποτε αποτέλεσμα
# (dict, list, pandas/numpy τιμές) σε "καθαρούς" Python τύπους (str, int,
# float, bool, None) που μπορούν πάντα να σερια-ποιηθούν με jsonify().
#
# Γιατί χρειάζεται: Το backtest/engine.py επιστρέφει DataFrames μετατρεμμένα
# σε dict (df.to_dict) και λίστες με pandas Timestamp / numpy.float64 /
# numpy.bool_ κλπ. Αυτοί οι τύποι ΔΕΝ είναι JSON-serializable από προεπιλογή
# -> το jsonify() στο app.py έσκαζε με TypeError ΕΚΤΟΣ του try/except του
# engine.py, οπότε το σφάλμα δεν καταγραφόταν ΠΟΥΘΕΝΑ στο log και ο χρήστης
# έβλεπε το γενικό μήνυμα "Υπήρξε σφάλμα ή καθυστέρηση στο δίκτυο."
# =============================================================================

import math
import numpy as np
import pandas as pd


def make_json_safe(value):
    """
    Επιστρέφει μια εκδοχή του value που είναι 100% ασφαλής για jsonify():
      - pandas.Timestamp / datetime -> ISO string
      - numpy αριθμητικοί τύποι (float64, int64, bool_) -> native Python
      - NaN / Inf -> None (η JSON δεν υποστηρίζει NaN/Infinity)
      - dict / list / tuple -> αναδρομική μετατροπή κάθε στοιχείου
      - οτιδήποτε άλλο -> επιστρέφεται όπως είναι
    """
    if isinstance(value, dict):
        return {k: make_json_safe(v) for k, v in value.items()}

    if isinstance(value, (list, tuple)):
        return [make_json_safe(v) for v in value]

    if isinstance(value, (pd.Timestamp, )) or isinstance(value, np.datetime64):
        return pd.Timestamp(value).isoformat()

    if isinstance(value, (np.bool_,)):
        return bool(value)

    if isinstance(value, (np.integer,)):
        return int(value)

    if isinstance(value, (np.floating, float)):
        f = float(value)
        return None if (math.isnan(f) or math.isinf(f)) else f

    return value
