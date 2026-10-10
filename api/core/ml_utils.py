"""
Общие ML-утилиты: метки риска, инкассация, метрики, калибровка.
Используются в train_model.py, predictor.py и check_prediction_accuracy.py.
"""

from __future__ import annotations

from typing import Any, Dict, Optional

import numpy as np

from .config import (
    CASHOUT_THRESHOLD,
    HIGH_RISK_THRESHOLD,
    HORIZON_HOURS,
    INCASSATION_PLANNED_PCT,
    INCASSATION_URGENT_PCT,
    LOW_BALANCE_METRIC_PCT,
    LOW_CASH_PCT,
    MIN_HISTORY_STEPS,
    WARNING_CASH_PCT,
)


def risk_label(prob: float, medium_threshold: float | None = None) -> str:
    med = CASHOUT_THRESHOLD if medium_threshold is None else medium_threshold
    if prob >= HIGH_RISK_THRESHOLD:
        return "HIGH"
    if prob >= med:
        return "MEDIUM"
    return "LOW"


def prediction_confidence(history_len: int, min_steps: int = MIN_HISTORY_STEPS) -> str:
    """HIGH / MEDIUM / LOW — насколько надёжен прогноз по длине истории."""
    if history_len >= min_steps:
        return "HIGH"
    if history_len >= max(min_steps // 2, 6):
        return "MEDIUM"
    return "LOW"


def incassation_recommendation(
    pred_balance_pct: float,
    current_balance_pct: float,
    cashout_prob: float,
    risk_label_value: str,
    hours_to_empty: Optional[float] = None,
) -> Dict[str, Any]:
    """
    Рекомендация по инкассации (правило поверх ML, без отдельной модели).
    """
    reasons = []

    if (
        pred_balance_pct < INCASSATION_URGENT_PCT * 100
        or current_balance_pct < LOW_CASH_PCT * 100
        or risk_label_value == "HIGH"
        or cashout_prob >= HIGH_RISK_THRESHOLD
    ):
        if pred_balance_pct < INCASSATION_URGENT_PCT * 100:
            reasons.append("прогноз < порога срочности")
        if current_balance_pct < LOW_CASH_PCT * 100:
            reasons.append("текущий остаток критичный")
        if risk_label_value == "HIGH":
            reasons.append("высокий ML-риск")
        return {
            "action": "URGENT",
            "priority": 1,
            "reason": "; ".join(reasons) or "критический уровень",
            "within_hours": 6,
        }

    if hours_to_empty is not None and hours_to_empty <= HORIZON_HOURS:
        reasons.append(f"до опустошения ~{hours_to_empty:.0f}ч")

    if (
        pred_balance_pct < INCASSATION_PLANNED_PCT * 100
        or current_balance_pct < WARNING_CASH_PCT * 100
        or risk_label_value == "MEDIUM"
        or cashout_prob >= CASHOUT_THRESHOLD
    ):
        if pred_balance_pct < INCASSATION_PLANNED_PCT * 100:
            reasons.append("прогноз ниже планового порога")
        if current_balance_pct < WARNING_CASH_PCT * 100:
            reasons.append("остаток в зоне предупреждения")
        if risk_label_value == "MEDIUM":
            reasons.append("средний ML-риск")
        return {
            "action": "PLANNED",
            "priority": 2,
            "reason": "; ".join(reasons) or "плановое пополнение",
            "within_hours": 24,
        }

    return {
        "action": "OK",
        "priority": 3,
        "reason": "пополнение не требуется в ближайшие 24ч",
        "within_hours": None,
    }


def regression_metrics(
    y_true,
    y_pred,
    capacity,
    low_pct_threshold: float = LOW_BALANCE_METRIC_PCT,
) -> Dict[str, float]:
    y_true = np.asarray(y_true, dtype=float)
    y_pred = np.asarray(y_pred, dtype=float)
    cap = np.asarray(capacity, dtype=float)
    cap = np.where(cap <= 0, 1, cap)

    pct_true = y_true / cap
    mae = float(np.mean(np.abs(y_true - y_pred)))
    rmse = float(np.sqrt(np.mean((y_true - y_pred) ** 2)))
    mape_cap = float(np.mean(np.abs((y_true - y_pred) / cap)) * 100)
    mape_actual = float(np.mean(np.abs((y_true - y_pred) / np.maximum(y_true, 1))) * 100)

    pct_err = np.abs((y_true - y_pred) / cap) * 100
    within_10 = float((pct_err <= 10).mean() * 100)
    within_20 = float((pct_err <= 20).mean() * 100)
    within_30 = float((pct_err <= 30).mean() * 100)

    low_mask = pct_true < low_pct_threshold
    low_mape = (
        float(np.mean(np.abs((y_true[low_mask] - y_pred[low_mask]) / cap[low_mask])) * 100)
        if low_mask.any()
        else float("nan")
    )

    return {
        "mae": mae,
        "rmse": rmse,
        "mape_capacity_pct": mape_cap,
        "mape_actual_pct": mape_actual,
        "within_10pct": within_10,
        "within_20pct": within_20,
        "within_30pct": within_30,
        "low_balance_mape_pct": low_mape,
        "low_balance_n": int(low_mask.sum()),
        "n": int(len(y_true)),
    }


def apply_calibrator(raw_prob: float, calibrator) -> float:
    if calibrator is None:
        return float(raw_prob)
    try:
        return float(calibrator.predict(np.asarray([raw_prob], dtype=float))[0])
    except Exception:
        return float(raw_prob)
