"""
Bashorat aniqligini tekshirish:
  A) Train pipeline (to'liq feature DF)
  B) Live pipeline (simulyator kabi 96 qadam)
  Natija: reports/metrics_latest.json
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import joblib
import numpy as np
import pandas as pd
from sklearn.metrics import (
    accuracy_score,
    average_precision_score,
    classification_report,
    confusion_matrix,
    roc_auc_score,
)

ROOT = Path(__file__).parent
sys.path.insert(0, str(ROOT / "api"))

from core.config import CASHOUT_THRESHOLD, INFERENCE_HISTORY_STEPS, VAL_END  # noqa: E402
from core.features import get_feature_names  # noqa: E402
from core.ml_inference import predict_balance  # noqa: E402
from core.ml_utils import (  # noqa: E402
    apply_calibrator,
    regression_metrics,
    risk_label,
)
from train_model import (  # noqa: E402
    encode_categoricals,
    load_data,
    make_feature_df,
)

SIM_COLS = [
    "transactionTime", "totalBalance", "totalOutcome", "totalIncome",
    "numberIncomeTransaction", "numberOutcomeTransaction", "totalNumberTransaction",
    "is_incassation", "is_breakdown", "low_cash_alert", "cash_utilization_pct",
]


def _load_inference_config() -> dict:
    path = ROOT / "models" / "inference_config.json"
    defaults = {
        "cashout_threshold": CASHOUT_THRESHOLD,
        "inference_history_steps": INFERENCE_HISTORY_STEPS,
        "use_atm_id_enc": True,
        "use_low_balance_model": True,
    }
    if not path.exists():
        return defaults
    return {**defaults, **json.loads(path.read_text(encoding="utf-8"))}


def classification_metrics(y_true, prob, threshold=CASHOUT_THRESHOLD):
    y_true = np.asarray(y_true, dtype=int)
    prob = np.asarray(prob, dtype=float)
    pred = (prob >= threshold).astype(int)

    acc = accuracy_score(y_true, pred) * 100
    roc = roc_auc_score(y_true, prob) if len(np.unique(y_true)) > 1 else float("nan")
    aucpr = average_precision_score(y_true, prob) if len(np.unique(y_true)) > 1 else float("nan")
    cm = confusion_matrix(y_true, pred, labels=[0, 1])

    report = classification_report(
        y_true, pred, target_names=["no_risk", "cash_out"],
        output_dict=True, zero_division=0,
    )

    risk_preds = np.array([risk_label(p) for p in prob])
    actual_high = y_true == 1
    pred_high_med = np.isin(risk_preds, ["HIGH", "MEDIUM"])
    risk_hit = (actual_high == pred_high_med).mean() * 100

    return {
        "accuracy_pct": acc,
        "roc_auc": roc,
        "auc_pr": aucpr,
        "precision_cashout_pct": report["cash_out"]["precision"] * 100,
        "recall_cashout_pct": report["cash_out"]["recall"] * 100,
        "f1_cashout_pct": report["cash_out"]["f1-score"] * 100,
        "risk_label_match_pct": risk_hit,
        "confusion": cm.tolist(),
        "pos_rate_pct": y_true.mean() * 100,
        "n": len(y_true),
    }


def print_reg(title: str, m: dict) -> None:
    print(f"\n{'='*60}")
    print(f"  {title}")
    print(f"{'='*60}")
    print(f"  Namunalar:              {m['n']:,}")
    print(f"  MAE:                    {m['mae']:,.0f} so'm")
    print(f"  RMSE:                   {m['rmse']:,.0f} so'm")
    print(f"  MAPE (kapasitet %):     {m['mape_capacity_pct']:.2f}%")
    print(f"  MAPE (haqiqiy balans %):{m['mape_actual_pct']:.2f}%")
    print(f"  MAPE (past balans):     {m['low_balance_mape_pct']:.2f}%  (n={m['low_balance_n']})")
    print(f"  ±10% ichida:            {m['within_10pct']:.1f}%")
    print(f"  ±20% ichida:            {m['within_20pct']:.1f}%")
    print(f"  ±30% ichida:            {m['within_30pct']:.1f}%")


def print_clf(title: str, m: dict) -> None:
    print(f"\n{'='*60}")
    print(f"  {title}")
    print(f"{'='*60}")
    print(f"  Namunalar:              {m['n']:,}")
    print(f"  Haqiqiy cash-out ulushi: {m['pos_rate_pct']:.1f}%")
    print(f"  Aniqlik:                {m['accuracy_pct']:.1f}%")
    print(f"  ROC-AUC:                {m['roc_auc']:.4f}")
    print(f"  Recall (cash-out):      {m['recall_cashout_pct']:.1f}%")
    tn, fp, fn, tp = np.array(m["confusion"]).ravel()
    print(f"  Confusion [TN={tn}, FP={fp}, FN={fn}, TP={tp}]")


def eval_train_pipeline(test_df, reg, reg_low, clf, calibrator, feature_cols, threshold):
    mask = test_df["target_balance_24h"].notna() & test_df["target_cashout_24h"].notna()
    sub = test_df.loc[mask]
    X = sub[feature_cols].fillna(0)
    y_reg = sub["target_balance_24h"].values
    y_clf = sub["target_cashout_24h"].astype(int).values
    cap = sub["atm_capacity"].values
    cur_pct = (sub["totalBalance"].values / cap) * 100

    pred_reg = np.array([
        predict_balance(X.iloc[[i]], reg, reg_low, cap[i], cur_pct[i])
        for i in range(len(sub))
    ])
    prob_raw = clf.predict_proba(X)[:, 1]
    prob_clf = np.array([apply_calibrator(p, calibrator) for p in prob_raw])
    return (
        regression_metrics(y_reg, pred_reg, cap),
        classification_metrics(y_clf, prob_clf, threshold=threshold),
    )


def build_prod_row(history, capacity, profile_enc, atm_id_enc, feats, include_atm_id, lat, lon):
    from core.features import make_features as prod_make_features
    df = prod_make_features(
        history.copy(),
        capacity=capacity,
        profile_enc=profile_enc,
        atm_id_enc=atm_id_enc,
        include_atm_id_enc=include_atm_id,
        lat=lat,
        lon=lon,
    )
    for f in feats:
        if f not in df.columns:
            df[f] = 0
    return df[feats].iloc[[-1]].fillna(0)


def eval_live_pipeline(
    raw_df, test_df, reg, reg_low, clf, calibrator, feats,
    profile_enc, id_enc, threshold, hist_n, include_atm_id,
):
    raw_df = raw_df.sort_values(["atmId", "transactionTime"]).reset_index(drop=True)
    grouped = {atm: g.reset_index(drop=True) for atm, g in raw_df.groupby("atmId", sort=False)}
    atm_geo = raw_df.groupby("atmId", sort=False).agg(lat=("lat", "first"), lon=("lon", "first"))

    mask = test_df["target_balance_24h"].notna() & test_df["target_cashout_24h"].notna()
    rows = test_df.loc[mask]

    pred_regs, pred_probs, y_regs, y_clfs, caps, cur_pcts = [], [], [], [], [], []

    for row in rows.itertuples(index=False):
        atm_id = row.atmId
        ts = row.transactionTime
        grp = grouped[atm_id]
        idx = grp.index[grp["transactionTime"] == ts]
        if len(idx) == 0:
            continue
        i = idx[0]
        start = max(0, i - (hist_n - 1))
        hist = grp.iloc[start:i + 1][SIM_COLS].copy()
        if len(hist) < 6:
            continue

        cap = int(row.atm_capacity)
        prof_enc = profile_enc.get(str(row.atmProfile), 0)
        atm_enc = id_enc.get(str(atm_id), 0)
        geo = atm_geo.loc[atm_id]
        lat, lon = float(geo["lat"]), float(geo["lon"])
        cur_bal = float(grp.iloc[i]["totalBalance"])
        cur_pct = cur_bal / cap * 100

        try:
            X_row = build_prod_row(hist, cap, prof_enc, atm_enc, feats, include_atm_id, lat, lon)
            pred_regs.append(predict_balance(X_row, reg, reg_low, cap, cur_pct))
            raw_p = float(clf.predict_proba(X_row)[0][1])
            pred_probs.append(apply_calibrator(raw_p, calibrator))
            y_regs.append(float(row.target_balance_24h))
            y_clfs.append(int(row.target_cashout_24h))
            caps.append(cap)
            cur_pcts.append(cur_pct)
        except Exception:
            continue

    return (
        regression_metrics(y_regs, pred_regs, caps),
        classification_metrics(y_clfs, pred_probs, threshold=threshold),
    )


def main():
    inf_cfg = _load_inference_config()
    clf_thr = float(inf_cfg.get("cashout_threshold", CASHOUT_THRESHOLD))
    hist_n = int(inf_cfg.get("inference_history_steps", INFERENCE_HISTORY_STEPS))
    include_atm_id = bool(inf_cfg.get("use_atm_id_enc", True))
    use_low = bool(inf_cfg.get("use_low_balance_model", True))

    print("Modellarni yuklash...")
    reg = joblib.load(ROOT / "models" / "xgb_regressor.joblib")
    clf = joblib.load(ROOT / "models" / "xgb_classifier.joblib")
    low_path = ROOT / "models" / "xgb_regressor_low.joblib"
    reg_low = joblib.load(low_path) if use_low and low_path.exists() else None
    cal_path = ROOT / "models" / "calibrator.joblib"
    calibrator = joblib.load(cal_path) if cal_path.exists() else None
    feats = json.loads((ROOT / "models" / "feature_names.json").read_text())

    print("Ma'lumotlarni tayyorlash...")
    raw = load_data()
    raw, le_map = encode_categoricals(raw)
    df = make_feature_df(raw, le_map, include_atm_id_enc=include_atm_id)
    feature_cols = get_feature_names(include_atm_id)

    profile_enc = {cls: i for i, cls in enumerate(le_map["atmProfile"].classes_)}
    id_enc = {cls: i for i, cls in enumerate(le_map["atmId"].classes_)}

    test = df[df["transactionTime"] > VAL_END].copy()
    test = test.dropna(subset=["target_balance_24h", "target_cashout_24h"])
    print(f"Test qatorlari: {len(test):,} | history={hist_n} | atmId_enc={include_atm_id}")

    reg_train, clf_train = eval_train_pipeline(
        test, reg, reg_low, clf, calibrator, feature_cols, clf_thr,
    )
    print_reg("A) TRAIN PIPELINE", reg_train)
    print_clf("A) TRAIN PIPELINE — cash-out", clf_train)

    print("\nLive pipeline baholanmoqda...")
    reg_live, clf_live = eval_live_pipeline(
        raw, test, reg, reg_low, clf, calibrator, feats,
        profile_enc, id_enc, clf_thr, hist_n, include_atm_id,
    )
    print_reg("B) LIVE PIPELINE", reg_live)
    print_clf("B) LIVE PIPELINE — cash-out", clf_live)

    drift_mape = reg_live["mape_capacity_pct"] - reg_train["mape_capacity_pct"]
    print(f"\n  TRAIN vs LIVE MAPE farqi: {drift_mape:+.2f} pp")

    if abs(drift_mape) < reg_train["mape_capacity_pct"] * 0.2:
        verdict = "OK — live va train yaqin"
    elif drift_mape > 0:
        verdict = "WARN — live biroz yomonroq"
    else:
        verdict = "OK"

    report = {
        "train_pipeline": {"regression": reg_train, "classification": clf_train},
        "live_pipeline": {"regression": reg_live, "classification": clf_live},
        "drift_mape_pp": round(drift_mape, 4),
        "verdict": verdict,
        "calibrator_used": calibrator is not None,
        "inference_config": inf_cfg,
    }
    out = ROOT / "reports" / "metrics_latest.json"
    with open(out, "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2, ensure_ascii=False)
    print(f"\n  Saqlandi: {out}")
    print(f"  Xulosa: {verdict}")


if __name__ == "__main__":
    main()
