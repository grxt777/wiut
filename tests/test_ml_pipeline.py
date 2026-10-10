"""ML pipeline unit tests."""

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "api"))

from core.config import (  # noqa: E402
    CASHOUT_THRESHOLD,
    HIGH_RISK_THRESHOLD,
    HORIZON_STEPS,
    MIN_HISTORY_STEPS,
)
from core.ml_utils import (  # noqa: E402
    incassation_recommendation,
    prediction_confidence,
    regression_metrics,
    risk_label,
)


def test_risk_label_thresholds():
    assert risk_label(0.1) == "LOW"
    assert risk_label(CASHOUT_THRESHOLD) == "MEDIUM"
    assert risk_label(HIGH_RISK_THRESHOLD) == "HIGH"


def test_prediction_confidence_levels():
    assert prediction_confidence(MIN_HISTORY_STEPS) == "HIGH"
    assert prediction_confidence(MIN_HISTORY_STEPS // 2) == "MEDIUM"
    assert prediction_confidence(2) == "LOW"


def test_incassation_urgent_on_high_risk():
    rec = incassation_recommendation(
        pred_balance_pct=15,
        current_balance_pct=50,
        cashout_prob=0.9,
        risk_label_value="HIGH",
    )
    assert rec["action"] == "URGENT"
    assert rec["priority"] == 1


def test_incassation_ok_when_safe():
    rec = incassation_recommendation(
        pred_balance_pct=70,
        current_balance_pct=65,
        cashout_prob=0.1,
        risk_label_value="LOW",
        hours_to_empty=48,
    )
    assert rec["action"] == "OK"


def test_regression_metrics_shape():
    import numpy as np
    y = np.array([100.0, 200.0, 300.0])
    pred = np.array([110.0, 190.0, 280.0])
    cap = np.array([400.0, 400.0, 400.0])
    m = regression_metrics(y, pred, cap)
    assert m["n"] == 3
    assert "low_balance_mape_pct" in m
    assert m["mape_capacity_pct"] >= 0


def test_horizon_steps():
    assert HORIZON_STEPS == 12


def test_feature_names_match_model():
    import json
    feat_path = ROOT / "models" / "feature_names.json"
    if not feat_path.exists():
        pytest.skip("models not trained")
    from core.features import get_feature_names
    inf_path = ROOT / "models" / "inference_config.json"
    use_atm_id = True
    if inf_path.exists():
        use_atm_id = bool(json.loads(inf_path.read_text()).get("use_atm_id_enc", True))
    saved = json.loads(feat_path.read_text())
    assert saved == get_feature_names(use_atm_id)
