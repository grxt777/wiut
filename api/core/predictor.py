"""
ML-инференс — live-прогноз на основе текущего состояния симулятора.
"""

from __future__ import annotations

import json
import logging
from functools import lru_cache
from typing import Dict, Optional, Tuple

import numpy as np
import pandas as pd

from .config import (
    CASHOUT_THRESHOLD,
    HIGH_RISK_THRESHOLD,
    INFERENCE_HISTORY_STEPS,
    MIN_HISTORY_STEPS,
    MODELS_DIR,
    PRED_PATH,
)
from .features import make_features
from .ml_inference import predict_balance
from .ml_utils import (
    apply_calibrator,
    incassation_recommendation,
    prediction_confidence,
    risk_label,
)

log = logging.getLogger(__name__)


@lru_cache(maxsize=1)
def _load_models():
    import joblib

    reg_path  = MODELS_DIR / "xgb_regressor.joblib"
    clf_path  = MODELS_DIR / "xgb_classifier.joblib"
    feat_path = MODELS_DIR / "feature_names.json"

    if not all(p.exists() for p in [reg_path, clf_path, feat_path]):
        log.warning("Модели не найдены в %s", MODELS_DIR)
        return None, None, None, None

    reg   = joblib.load(reg_path)
    clf   = joblib.load(clf_path)
    feats = json.loads(feat_path.read_text())
    low_path = MODELS_DIR / "xgb_regressor_low.joblib"
    reg_low = joblib.load(low_path) if low_path.exists() else None
    log.info("Models loaded: %d features, low_balance=%s", len(feats), reg_low is not None)
    return reg, reg_low, clf, feats


@lru_cache(maxsize=1)
def _load_inference_config() -> dict:
    path = MODELS_DIR / "inference_config.json"
    defaults = {
        "cashout_threshold": CASHOUT_THRESHOLD,
        "high_risk_threshold": HIGH_RISK_THRESHOLD,
        "inference_history_steps": INFERENCE_HISTORY_STEPS,
        "use_atm_id_enc": True,
        "use_low_balance_model": True,
    }
    if not path.exists():
        return defaults
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return {**defaults, **data}
    except Exception:
        return defaults


def _cashout_threshold() -> float:
    return float(_load_inference_config().get("cashout_threshold", CASHOUT_THRESHOLD))


def _history_steps() -> int:
    return int(_load_inference_config().get("inference_history_steps", INFERENCE_HISTORY_STEPS))


@lru_cache(maxsize=1)
def _load_calibrator():
    import joblib
    path = MODELS_DIR / "calibrator.joblib"
    if not path.exists():
        return None
    try:
        return joblib.load(path)
    except Exception:
        return None


@lru_cache(maxsize=1)
def _load_encoders() -> Tuple[Dict[str, int], Dict[str, int]]:
    path = MODELS_DIR / "encoders.json"
    if not path.exists():
        return {}, {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        profile = {str(k): int(v) for k, v in data.get("atmProfile", {}).items()}
        atm_ids = {str(k): int(v) for k, v in data.get("atmId", {}).items()}
        return profile, atm_ids
    except Exception:
        return {}, {}


@lru_cache(maxsize=1)
def _load_static_predictions() -> Dict[str, dict]:
    if not PRED_PATH.exists():
        return {}
    df = pd.read_csv(PRED_PATH)
    result = {}
    for _, row in df.iterrows():
        result[str(row["atmId"])] = {
            "atm_id": str(row["atmId"]),
            "pred_balance_24h": float(row.get("pred_balance_24h", 0)),
            "pred_balance_pct": float(row.get("pred_balance_pct_24h", 0)),
            "cashout_prob": float(row.get("pred_cashout_prob", 0)),
            "cashout_risk": bool(row.get("pred_cashout_risk", False)),
            "risk_label": str(row.get("risk_label", "LOW")),
            "prediction_confidence": "MEDIUM",
            "incassation": {"action": "OK", "priority": 3, "reason": "static fallback"},
        }
    return result


_live_predictions: Dict[str, dict] = {}
_profile_enc: Dict[str, int] = {}
_id_enc: Dict[str, int] = {}


def reload_models() -> None:
    _load_models.cache_clear()
    _load_inference_config.cache_clear()
    _load_calibrator.cache_clear()
    _load_encoders.cache_clear()
    _load_static_predictions.cache_clear()
    global _profile_enc, _id_enc
    _profile_enc = {}
    _id_enc = {}


def _ensure_encoders(meta_dict: dict) -> None:
    global _profile_enc, _id_enc
    if _profile_enc and _id_enc:
        return
    saved_profile, saved_ids = _load_encoders()
    if saved_profile and saved_ids:
        _profile_enc = saved_profile
        _id_enc = saved_ids
        return
    profiles = sorted({v.get("profile", "residential") for v in meta_dict.values()})
    ids = sorted(meta_dict.keys())
    _profile_enc = {p: i for i, p in enumerate(profiles)}
    _id_enc = {a: i for i, a in enumerate(ids)}


def refresh_predictions(simulator) -> None:
    global _live_predictions

    reg, reg_low, clf, feats = _load_models()
    if reg is None:
        _live_predictions = dict(_load_static_predictions())
        return

    cfg = _load_inference_config()
    if not cfg.get("use_low_balance_model", True):
        reg_low = None

    calibrator = _load_calibrator()
    include_atm_id = bool(cfg.get("use_atm_id_enc", True))
    hist_n = _history_steps()
    meta_all = simulator.get_all_meta()
    _ensure_encoders(meta_all)

    updated = 0
    for atm_id, meta in meta_all.items():
        records = simulator.get_history(atm_id, last_n=hist_n)
        if not records:
            continue

        try:
            history_len = len(records)
            df = pd.DataFrame(records)
            df = make_features(
                df,
                capacity=meta.get("capacity", 1),
                profile_enc=_profile_enc.get(meta.get("profile", "residential"), 0),
                atm_id_enc=_id_enc.get(atm_id, 0),
                include_atm_id_enc=include_atm_id,
                lat=meta.get("lat"),
                lon=meta.get("lon"),
            )

            for f in feats:
                if f not in df.columns:
                    df[f] = 0
            row = df[feats].iloc[[-1]].fillna(0)

            cap = meta.get("capacity", 1) or 1
            state = simulator.get_state(atm_id) or {}
            current_bal = float(state.get("balance", records[-1].get("totalBalance", 0)))
            current_pct = current_bal / cap * 100
            hours_empty = state.get("hours_to_empty")

            pred_bal = predict_balance(row, reg, reg_low, cap, current_pct)
            raw_prob = float(clf.predict_proba(row)[0][1])
            cashout_prob = apply_calibrator(raw_prob, calibrator)
            pred_pct = pred_bal / cap * 100
            thr = _cashout_threshold()
            label = risk_label(cashout_prob, medium_threshold=thr)
            conf = prediction_confidence(history_len, MIN_HISTORY_STEPS)
            inc = incassation_recommendation(
                pred_balance_pct=pred_pct,
                current_balance_pct=current_pct,
                cashout_prob=cashout_prob,
                risk_label_value=label,
                hours_to_empty=hours_empty,
            )

            _live_predictions[atm_id] = {
                "atm_id": atm_id,
                "pred_balance_24h": round(pred_bal),
                "pred_balance_pct": round(pred_pct, 1),
                "cashout_prob": round(cashout_prob, 4),
                "cashout_prob_raw": round(raw_prob, 4),
                "cashout_risk": cashout_prob >= thr,
                "risk_label": label,
                "prediction_confidence": conf,
                "history_steps": history_len,
                "regressor_used": "low" if reg_low and current_pct < 30 else "main",
                "incassation": inc,
            }
            updated += 1
        except Exception as exc:
            log.debug("Инференс %s: %s", atm_id, exc)
            continue

    if updated:
        log.debug("Live-прогнозы: %d ATM (history=%d steps)", updated, hist_n)
    elif not _live_predictions:
        _live_predictions = dict(_load_static_predictions())


def get_prediction(atm_id: str) -> Optional[dict]:
    return _live_predictions.get(atm_id)


def get_all_predictions() -> Dict[str, dict]:
    if not _live_predictions:
        return dict(_load_static_predictions())
    return dict(_live_predictions)
