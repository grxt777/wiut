"""
Двойная регрессия: основная + low-balance модель.
"""

from __future__ import annotations

from typing import Optional

import numpy as np

from .config import LOW_BALANCE_TRAIN_PCT


def predict_balance(
    row,
    reg_main,
    reg_low: Optional[object],
    capacity: float,
    current_balance_pct: float,
) -> float:
    """Если остаток низкий — специализированная модель, иначе основная."""
    cap = capacity or 1
    if reg_low is not None and current_balance_pct < LOW_BALANCE_TRAIN_PCT * 100:
        pred = float(reg_low.predict(row)[0])
    else:
        pred = float(reg_main.predict(row)[0])
    return float(np.clip(pred, 0, cap))
