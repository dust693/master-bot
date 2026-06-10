from dataclasses import dataclass, field
from typing import Optional
import pandas as pd
import config
from indicators.technical_analysis import calculate_all_indicators
from utils.logger import logger

@dataclass
class Signal:
    symbol: str
    action: int
    price: float
    confidence: float
    signals: dict = field(default_factory=dict)
    stop_loss: Optional[float] = None
    take_profit: Optional[float] = None
    reason: str = ""

    @property
    def action_str(self) -> str:
        return {1: "BUY", -1: "SELL", 0: "HOLD"}.get(self.action, "HOLD")

class StrategyManager:
    # Παράγεται αυτόματα από το config.INDICATOR_CATEGORIES.
    # Αντιστοιχεί κάθε δείκτη στη στήλη signal του DataFrame.
    # Ο ATR εξαιρείται (col = None) γιατί δεν παράγει signal.
    SIGNAL_COLUMNS = {
        ind: col
        for inds in config.INDICATOR_CATEGORIES.values()
        for ind, col in inds.items()
        if col is not None
    }

    def __init__(self, strategy_cfg: dict = None):
        self.strategy_cfg = strategy_cfg or config.STRATEGY_CONFIG
        self.combination  = self.strategy_cfg.get("COMBINATION_LOGIC", "AND")
        self.active_indicators = [name for name, cfg in self.strategy_cfg.items() if isinstance(cfg, dict) and cfg.get("enabled", False)]

    def analyze(self, df: pd.DataFrame, symbol: str) -> Signal:
        if df is None or df.empty:
            return Signal(symbol=symbol, action=0, price=0, confidence=0)

        df = calculate_all_indicators(df, self.strategy_cfg)
        last = df.iloc[-1]
        current_price = float(last["close"])

        individual_signals: dict[str, int] = {}

        for indicator in self.active_indicators:
            col = self.SIGNAL_COLUMNS.get(indicator)
            if col and col in df.columns:
                sig_val = int(last[col]) if pd.notna(last[col]) else 0
                individual_signals[indicator] = sig_val
                
                # --- ΝΕΟ: ΑΝΑΛΥΤΙΚΟ DEBUG ΜΕ ΠΑΡΑΜΕΤΡΟΥΣ ---
                cfg = self.strategy_cfg.get(indicator, {})
                params_str = ", ".join([f"{k}={v}" for k, v in cfg.items() if k != 'enabled'])
                sig_str = 'BUY' if sig_val == 1 else 'SELL' if sig_val == -1 else 'HOLD'
                logger.debug(f"  [ΔΕΙΚΤΗΣ] {indicator} ({params_str}) -> {sig_str}")
                # --------------------------------------------
            else:
                logger.debug(f"  [ΔΕΙΚΤΗΣ] {indicator}: Δεν υπολογίστηκε (λείπουν δεδομένα/στήλη)")

        action, confidence = self._combine_signals(individual_signals)
        stop_loss, take_profit = self._calc_sl_tp(df, current_price, action)
        reason = self._build_reason(individual_signals, action)

        return Signal(symbol=symbol, action=action, price=current_price, confidence=confidence,
                      signals=individual_signals, stop_loss=stop_loss, take_profit=take_profit, reason=reason)

    def _combine_signals(self, signals: dict[str, int]) -> tuple[int, float]:
        valid = {k: v for k, v in signals.items() if v != 0}
        total = len(signals)
        if not valid: return 0, 0.0

        buy_count  = sum(1 for v in valid.values() if v ==  1)
        sell_count = sum(1 for v in valid.values() if v == -1)

        if self.combination == "AND":
            if buy_count == len(valid) and sell_count == 0: return 1, buy_count / total
            elif sell_count == len(valid) and buy_count == 0: return -1, sell_count / total
        elif self.combination == "OR":
            if buy_count > sell_count: return 1, buy_count / total
            elif sell_count > buy_count: return -1, sell_count / total
        elif self.combination == "MAJORITY":
            if buy_count > sell_count and buy_count > total * 0.5: return 1, buy_count / total
            elif sell_count > buy_count and sell_count > total * 0.5: return -1, sell_count / total
        return 0, 0.0

    def _calc_sl_tp(self, df: pd.DataFrame, price: float, action: int) -> tuple[Optional[float], Optional[float]]:
        if action == 0 or price == 0: return None, None
        sl_pct, tp_pct = config.STOP_LOSS_PCT, config.TAKE_PROFIT_PCT
        if action == 1: return price * (1 - sl_pct), price * (1 + tp_pct)
        else: return price * (1 + sl_pct), price * (1 - tp_pct)

    def _build_reason(self, signals: dict[str, int], action: int) -> str:
        if action == 0: return "Ουδέτερο (Δεν ικανοποιήθηκε η λογική)"
        buying = [k for k, v in signals.items() if v == 1]
        selling = [k for k, v in signals.items() if v == -1]
        parts = []
        if buying: parts.append(f"BUY: {', '.join(buying)}")
        if selling: parts.append(f"SELL: {', '.join(selling)}")
        return " | ".join(parts)

    def _update_trailing_stop(self, df: pd.DataFrame, entry_price: float, current_high: float,
                              position_type: str = "long") -> dict:
        """
        Υπολογίζει το Trailing Stop για μια ανοιχτή θέση.
        Επιστρέφει: {'stop_price': float, 'activated': bool, 'reason': str}
        """
        if not config.TRAILING_STOP_CONFIG.get("enabled", False):
            return {"stop_price": None, "activated": False, "reason": "Trailing Stop απενεργοποιημένο"}

        cfg = config.TRAILING_STOP_CONFIG
        current_price = float(df.iloc[-1]['close'])
        atr = float(df.iloc[-1].get('ATR', 0))

        # Υπολογισμός activation
        profit_pct = (current_price - entry_price) / entry_price if position_type == "long" else 0
        activated = profit_pct >= cfg["activation_pct"]

        if not activated:
            return {
                "stop_price": entry_price * (1 - config.STOP_LOSS_PCT),
                "activated": False,
                "reason": f"Δεν ενεργοποιήθηκε ακόμα ({profit_pct*100:.2f}%)"
            }

        # Υπολογισμός Trailing Stop
        if cfg["method"] == "percent":
            trail_stop = current_high * (1 - cfg["trail_pct"])
        elif cfg["method"] == "atr" and atr > 0:
            trail_stop = current_high - (atr * cfg["atr_multiplier"])
        else:
            trail_stop = current_high * (1 - cfg["trail_pct"])

        # Διατήρηση fixed SL ως πάτωμα
        if cfg["use_alongside_fixed_sl"]:
            fixed_sl = entry_price * (1 - config.STOP_LOSS_PCT)
            trail_stop = max(trail_stop, fixed_sl)

        return {
            "stop_price": trail_stop,
            "activated": True,
            "reason": f"Trailing Stop ({cfg['method']}) @ {trail_stop:.4f}"
        }