import os, sys
os.environ.setdefault("PYTHONIOENCODING", "utf-8")
if hasattr(sys.stdout, "reconfigure"): sys.stdout.reconfigure(encoding="utf-8")

"""ATM Cash-Out Prediction Pipeline — optimized training."""

import json
import warnings
from datetime import datetime, timezone
from pathlib import Path

import joblib
import numpy as np
import pandas as pd
from sklearn.isotonic import IsotonicRegression
from sklearn.metrics import (
    average_precision_score,
    classification_report,
    precision_recall_curve,
    roc_auc_score,
)
from sklearn.preprocessing import LabelEncoder
import xgboost as xgb

warnings.filterwarnings("ignore")

sys.path.insert(0, str(Path(__file__).parent / "api"))
from core.config import (  # noqa: E402
    CASHOUT_THRESHOLD,
    EARLY_STOPPING_ROUNDS,
    HIGH_RISK_THRESHOLD,
    HORIZON_HOURS,
    HORIZON_STEPS,
    HYPERPARAM_TUNING_TRIALS,
    INFERENCE_HISTORY_STEPS,
    LOW_BALANCE_TRAIN_PCT,
    LOW_CASH_PCT,
    MODELS_DIR,
    TRAIN_END,
    VAL_END,
    XGB_LEARNING_RATE,
    XGB_MAX_DEPTH,
    XGB_N_ESTIMATORS,
)
from core.features import make_features as build_features, get_feature_names  # noqa: E402
from core.calendar_uz import SALARY_DAYS  # noqa: E402
from core.ml_inference import predict_balance  # noqa: E402
from core.ml_utils import regression_metrics, risk_label  # noqa: E402

print("[OK] unified features.py | SALARY_DAYS =", sorted(SALARY_DAYS))

PRED_DIR   = Path("predictions"); PRED_DIR.mkdir(exist_ok=True)
REPORT_DIR = Path("reports");     REPORT_DIR.mkdir(exist_ok=True)
MODELS_DIR.mkdir(exist_ok=True)
DATA_PATH = Path("atm_transactions_enriched.csv")

SEARCH_SPACE = {
    "max_depth":        [5, 6, 7, 8],
    "learning_rate":    [0.03, 0.05, 0.07],
    "subsample":        [0.75, 0.8, 0.9],
    "colsample_bytree": [0.75, 0.8, 0.9],
    "min_child_weight": [3, 5, 7],
    "reg_alpha":        [0.05, 0.1, 0.2],
}


def _xgb_params(overrides: dict | None = None) -> dict:
    p = {
        "n_estimators":          XGB_N_ESTIMATORS,
        "learning_rate":         XGB_LEARNING_RATE,
        "max_depth":             XGB_MAX_DEPTH,
        "subsample":             0.8,
        "colsample_bytree":      0.8,
        "min_child_weight":      5,
        "reg_alpha":             0.1,
        "reg_lambda":            1.0,
        "tree_method":           "hist",
        "device":                "cpu",
        "random_state":          42,
        "n_jobs":                -1,
        "early_stopping_rounds": EARLY_STOPPING_ROUNDS,
    }
    if overrides:
        p.update(overrides)
    return p


def load_data() -> pd.DataFrame:
    print("Загружаем данные...")
    df = pd.read_csv(DATA_PATH, parse_dates=["transactionTime"])
    df = df.sort_values(["atmId", "transactionTime"]).reset_index(drop=True)
    df = df[df["is_breakdown"] == 0].copy()
    print(f"  Строк: {len(df):,} | ATM: {df['atmId'].nunique()}")
    return df


def encode_categoricals(df: pd.DataFrame) -> tuple[pd.DataFrame, dict]:
    le_map = {}
    for col in ["atmProfile", "atmId"]:
        le = LabelEncoder()
        df[col + "_enc"] = le.fit_transform(df[col].astype(str))
        le_map[col] = le
    return df, le_map


def make_feature_df(df: pd.DataFrame, le_map: dict, include_atm_id_enc: bool) -> pd.DataFrame:
    le_profile = le_map["atmProfile"]
    le_atm     = le_map["atmId"]
    dfs = []
    for atm_id, grp in df.groupby("atmId", sort=False):
        grp = grp.copy().reset_index(drop=True)
        cap = int(grp["atm_capacity"].iloc[0])
        profile = str(grp["atmProfile"].iloc[0])
        prof_enc = int(le_profile.transform([profile])[0])
        atm_enc  = int(le_atm.transform([str(atm_id)])[0])
        lat = float(grp["lat"].iloc[0]) if "lat" in grp.columns else None
        lon = float(grp["lon"].iloc[0]) if "lon" in grp.columns else None
        feat = build_features(
            grp, capacity=cap, profile_enc=prof_enc, atm_id_enc=atm_enc,
            include_atm_id_enc=include_atm_id_enc, lat=lat, lon=lon,
        )
        bal = feat["totalBalance"].astype(float)
        pct = bal / cap
        feat["target_balance_24h"] = bal.shift(-HORIZON_STEPS)
        feat["target_pct_24h"]     = pct.shift(-HORIZON_STEPS)
        future_low = pd.concat(
            [pct.shift(-s) for s in range(1, HORIZON_STEPS + 1)], axis=1
        ).min(axis=1)
        feat["target_cashout_24h"] = (future_low < LOW_CASH_PCT).astype(int)
        dfs.append(feat)
    return pd.concat(dfs, ignore_index=True)


def temporal_split(df: pd.DataFrame):
    train = df[df["transactionTime"] <= TRAIN_END].copy()
    val   = df[(df["transactionTime"] > TRAIN_END) & (df["transactionTime"] <= VAL_END)].copy()
    test  = df[df["transactionTime"] > VAL_END].copy()
    print(f"\nРазбивка: train {len(train):,} | val {len(val):,} | test {len(test):,}")
    return train, val, test


def _val_regression_score(train, val, feature_cols, xgb_overrides: dict | None = None) -> dict:
    mask_tr = train["target_balance_24h"].notna()
    mask_va = val["target_balance_24h"].notna()
    X_tr = train.loc[mask_tr, feature_cols].fillna(0)
    y_tr = train.loc[mask_tr, "target_balance_24h"]
    X_va = val.loc[mask_va, feature_cols].fillna(0)
    y_va = val.loc[mask_va, "target_balance_24h"]
    cap_va = val.loc[mask_va, "atm_capacity"].values

    model = xgb.XGBRegressor(**_xgb_params(xgb_overrides))
    model.fit(X_tr, y_tr, eval_set=[(X_va, y_va)], verbose=0)
    pred = model.predict(X_va)
    m = regression_metrics(y_va.values, pred, cap_va)
    m["best_iteration"] = int(getattr(model, "best_iteration", XGB_N_ESTIMATORS))
    return m


def tune_hyperparameters(train, val, feature_cols) -> dict:
    print(f"\n- Hyperparameter tuning ({HYPERPARAM_TUNING_TRIALS} trials) -")
    rng = np.random.default_rng(42)
    best_mape = float("inf")
    best_params: dict = {}

    for t in range(HYPERPARAM_TUNING_TRIALS):
        trial_params = {k: rng.choice(v) for k, v in SEARCH_SPACE.items()}
        m = _val_regression_score(train, val, feature_cols, trial_params)
        score = m["mape_capacity_pct"] + 0.2 * m["low_balance_mape_pct"]
        if score < best_mape:
            best_mape = score
            best_params = trial_params
        if (t + 1) % 5 == 0:
            print(f"  trial {t+1}/{HYPERPARAM_TUNING_TRIALS} | best composite: {best_mape:.2f}")

    print(f"  Best params: {best_params}")
    return best_params


def ab_test_atm_id_enc(raw, le_map) -> tuple[bool, dict]:
    print("\n- A/B: atmId_enc vs geo+profile only -")
    df_with = make_feature_df(raw, le_map, include_atm_id_enc=True)
    df_without = make_feature_df(raw, le_map, include_atm_id_enc=False)
    cols_with = get_feature_names(True)
    cols_without = get_feature_names(False)

    tr_w, va_w, _ = temporal_split(df_with)
    tr_wo, va_wo, _ = temporal_split(df_without)
    tr_w = tr_w.dropna(subset=["target_balance_24h", "target_cashout_24h"])
    va_w = va_w.dropna(subset=["target_balance_24h", "target_cashout_24h"])
    tr_wo = tr_wo.dropna(subset=["target_balance_24h", "target_cashout_24h"])
    va_wo = va_wo.dropna(subset=["target_balance_24h", "target_cashout_24h"])

    quick = {"n_estimators": 400}
    m_with = _val_regression_score(tr_w, va_w, cols_with, quick)
    m_without = _val_regression_score(tr_wo, va_wo, cols_without, quick)

    def composite(m):
        return m["mape_capacity_pct"] + 0.25 * m["low_balance_mape_pct"]

    score_with = composite(m_with)
    score_without = composite(m_without)
    # geo-only если не хуже более чем на 0.15pp — лучше для новых ATM
    use_atm_id = score_with < score_without - 0.15

    report = {
        "with_atm_id_enc": {
            "mape_capacity_pct": round(m_with["mape_capacity_pct"], 3),
            "low_balance_mape_pct": round(m_with["low_balance_mape_pct"], 3),
            "composite": round(score_with, 3),
        },
        "without_atm_id_enc": {
            "mape_capacity_pct": round(m_without["mape_capacity_pct"], 3),
            "low_balance_mape_pct": round(m_without["low_balance_mape_pct"], 3),
            "composite": round(score_without, 3),
        },
        "selected": "with_atm_id_enc" if use_atm_id else "geo_profile_only",
    }
    print(f"  WITH atmId:    MAPE {m_with['mape_capacity_pct']:.2f}% | low {m_with['low_balance_mape_pct']:.2f}%")
    print(f"  WITHOUT atmId: MAPE {m_without['mape_capacity_pct']:.2f}% | low {m_without['low_balance_mape_pct']:.2f}%")
    print(f"  → Selected: {report['selected']}")
    return use_atm_id, report


def train_regressor(train, val, feature_cols, xgb_overrides: dict | None = None, label: str = ""):
    title = f"Regression {label}".strip()
    print(f"\n- Task A: {title or 'Regression (balance_24h)'} -")
    mask_tr = train["target_balance_24h"].notna()
    mask_va = val["target_balance_24h"].notna()
    X_tr = train.loc[mask_tr, feature_cols].fillna(0)
    y_tr = train.loc[mask_tr, "target_balance_24h"]
    X_va = val.loc[mask_va, feature_cols].fillna(0)
    y_va = val.loc[mask_va, "target_balance_24h"]
    cap_va = val.loc[mask_va, "atm_capacity"]

    model = xgb.XGBRegressor(**_xgb_params(xgb_overrides))
    model.fit(X_tr, y_tr, eval_set=[(X_va, y_va)], verbose=100)
    pred_va = model.predict(X_va)
    m = regression_metrics(y_va.values, pred_va, cap_va.values)
    print(f"  Val MAPE: {m['mape_capacity_pct']:.2f}% | low-bal: {m['low_balance_mape_pct']:.2f}% | iter {model.best_iteration}")
    return model, m


def train_low_balance_regressor(train, val, feature_cols, xgb_overrides: dict | None = None):
    print("\n- Task A2: Low-balance regression (<30%) -")
    tr_pct = train["totalBalance"] / train["atm_capacity"].replace(0, 1)
    va_pct = val["totalBalance"] / val["atm_capacity"].replace(0, 1)
    tr_low = train[tr_pct < LOW_BALANCE_TRAIN_PCT]
    va_low = val[va_pct < LOW_BALANCE_TRAIN_PCT]

    if len(tr_low) < 500 or len(va_low) < 50:
        print("  Недостаточно low-balance строк — пропуск")
        return None, {}

    mask_tr = tr_low["target_balance_24h"].notna()
    mask_va = va_low["target_balance_24h"].notna()
    model = xgb.XGBRegressor(**_xgb_params(xgb_overrides))
    model.fit(
        tr_low.loc[mask_tr, feature_cols].fillna(0),
        tr_low.loc[mask_tr, "target_balance_24h"],
        eval_set=[(
            va_low.loc[mask_va, feature_cols].fillna(0),
            va_low.loc[mask_va, "target_balance_24h"],
        )],
        verbose=100,
    )
    pred = model.predict(va_low.loc[mask_va, feature_cols].fillna(0))
    m = regression_metrics(
        va_low.loc[mask_va, "target_balance_24h"].values,
        pred,
        va_low.loc[mask_va, "atm_capacity"].values,
    )
    print(f"  Val low-balance MAPE: {m['mape_capacity_pct']:.2f}% (n={m['n']})")
    return model, m


def train_classifier(train, val, feature_cols, xgb_overrides: dict | None = None):
    print("\n- Task B: Classification (cash_out_risk_24h) -")
    mask_tr = train["target_cashout_24h"].notna()
    mask_va = val["target_cashout_24h"].notna()
    X_tr = train.loc[mask_tr, feature_cols].fillna(0)
    y_tr = train.loc[mask_tr, "target_cashout_24h"].astype(int)
    X_va = val.loc[mask_va, feature_cols].fillna(0)
    y_va = val.loc[mask_va, "target_cashout_24h"].astype(int)

    spw = max((y_tr == 0).sum() / max((y_tr == 1).sum(), 1), 1.0)
    params = {**_xgb_params(xgb_overrides), "scale_pos_weight": spw, "eval_metric": "aucpr"}
    model = xgb.XGBClassifier(**params)
    model.fit(X_tr, y_tr, eval_set=[(X_va, y_va)], verbose=100)

    prob_va = model.predict_proba(X_va)[:, 1]
    roc = roc_auc_score(y_va, prob_va)
    aucpr = average_precision_score(y_va, prob_va)
    print(f"  Val ROC-AUC: {roc:.4f} | AUC-PR: {aucpr:.4f}")
    return model, {"roc_auc": roc, "auc_pr": aucpr, "scale_pos_weight": spw}


def fit_calibrator(prob_val, y_val):
    if len(np.unique(y_val)) < 2:
        return None
    cal = IsotonicRegression(out_of_bounds="clip")
    cal.fit(prob_val, y_val)
    return cal


def tune_threshold(prob_val, y_val, default=CASHOUT_THRESHOLD) -> float:
    raw_pred = (prob_val >= default).astype(int)
    raw_recall = float((raw_pred[y_val == 1]).mean()) if (y_val == 1).any() else 0.0
    target_recall = max(raw_recall - 0.02, 0.75)
    prec, rec, thresh = precision_recall_curve(y_val, prob_val)
    best_t, best_f1 = default, -1.0
    for p, r, t in zip(prec[:-1], rec[:-1], thresh):
        if r < target_recall:
            continue
        f1 = 2 * p * r / max(p + r, 1e-9)
        if f1 > best_f1:
            best_t, best_f1 = f1, float(t)
    return round(best_t, 4)


def print_feature_importance(model, feature_cols, label=""):
    imp = pd.Series(model.feature_importances_, index=feature_cols)
    top = imp.nlargest(12)
    print(f"\n  Top features ({label}):")
    for feat, score in top.items():
        print(f"    {feat:<32} {score:.4f}")
    return imp.to_dict()


def evaluate_on_test(reg_model, reg_low, clf_model, calibrator, test, feature_cols, clf_threshold):
    print("\n- Финальная оценка на TEST -")
    mask = test["target_balance_24h"].notna() & test["target_cashout_24h"].notna()
    sub = test.loc[mask]
    X_te = sub[feature_cols].fillna(0)
    y_te_reg = sub["target_balance_24h"].values
    y_te_clf = sub["target_cashout_24h"].astype(int).values
    cap_te = sub["atm_capacity"].values
    cur_pct = (sub["totalBalance"].values / cap_te) * 100

    pred_reg = np.array([
        predict_balance(X_te.iloc[[i]], reg_model, reg_low, cap_te[i], cur_pct[i])
        for i in range(len(sub))
    ])
    prob_raw = clf_model.predict_proba(X_te)[:, 1]
    prob_clf = calibrator.predict(prob_raw) if calibrator is not None else prob_raw
    pred_clf = (prob_clf >= clf_threshold).astype(int)

    reg_m = regression_metrics(y_te_reg, pred_reg, cap_te)
    low_mask = (sub["totalBalance"].values / cap_te) < LOW_BALANCE_TRAIN_PCT
    if low_mask.any():
        reg_low_only = regression_metrics(
            y_te_reg[low_mask], pred_reg[low_mask], cap_te[low_mask]
        )
        print(f"  [Regression]  MAPE: {reg_m['mape_capacity_pct']:.2f}% | ±20%: {reg_m['within_20pct']:.1f}%")
        print(f"  [Low-zone]    MAPE: {reg_low_only['mape_capacity_pct']:.2f}% (n={reg_low_only['n']})")
    else:
        print(f"  [Regression]  MAPE: {reg_m['mape_capacity_pct']:.2f}%")

    roc = roc_auc_score(y_te_clf, prob_clf)
    aucpr = average_precision_score(y_te_clf, prob_clf)
    print(f"  [Classifier]  ROC-AUC: {roc:.4f} | recall@thr: ", end="")
    print(classification_report(y_te_clf, pred_clf, target_names=["no", "risk"], digits=3).split("\n")[-3])

    return {**{f"test_{k}": v for k, v in reg_m.items()}, "test_roc_auc": roc, "test_auc_pr": aucpr}


def walk_forward_monthly(reg_model, reg_low, clf_model, calibrator, df, feature_cols, clf_threshold):
    test = df[df["transactionTime"] > VAL_END].dropna(subset=["target_balance_24h", "target_cashout_24h"])
    if test.empty:
        return []
    test = test.copy()
    test["_eval_month"] = test["transactionTime"].dt.to_period("M").astype(str)
    rows = []
    for month, grp in test.groupby("_eval_month"):
        cap = grp["atm_capacity"].values
        cur_pct = grp["totalBalance"].values / cap * 100
        X = grp[feature_cols].fillna(0)
        pred = np.array([
            predict_balance(X.iloc[[i]], reg_model, reg_low, cap[i], cur_pct[i])
            for i in range(len(grp))
        ])
        rm = regression_metrics(grp["target_balance_24h"].values, pred, cap)
        prob = clf_model.predict_proba(X)[:, 1]
        if calibrator is not None:
            prob = calibrator.predict(prob)
        y_clf = grp["target_cashout_24h"].astype(int).values
        rows.append({
            "month": month, "n": len(grp),
            "mape_capacity_pct": round(rm["mape_capacity_pct"], 2),
            "within_20pct": round(rm["within_20pct"], 1),
            "roc_auc": round(float(roc_auc_score(y_clf, prob)), 4) if len(np.unique(y_clf)) > 1 else None,
        })
    return rows


def _json_safe(obj):
    """numpy/sklearn tiplarini JSON uchun native Python ga."""
    if isinstance(obj, dict):
        return {k: _json_safe(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_json_safe(v) for v in obj]
    if isinstance(obj, (np.integer,)):
        return int(obj)
    if isinstance(obj, (np.floating,)):
        return float(obj)
    if isinstance(obj, np.ndarray):
        return obj.tolist()
    return obj


def save_encoders(le_map):
    encoders = {col: {str(c): int(i) for i, c in enumerate(le.classes_)} for col, le in le_map.items()}
    with open(MODELS_DIR / "encoders.json", "w", encoding="utf-8") as f:
        json.dump(encoders, f, indent=2, ensure_ascii=False)


def save_artifacts(
    reg_model, reg_low, clf_model, calibrator, feature_cols, metrics, le_map,
    reg_imp, clf_imp, walk_forward, clf_threshold, best_params, ab_report, low_metrics,
):
    joblib.dump(reg_model, MODELS_DIR / "xgb_regressor.joblib")
    joblib.dump(clf_model, MODELS_DIR / "xgb_classifier.joblib")
    if reg_low is not None:
        joblib.dump(reg_low, MODELS_DIR / "xgb_regressor_low.joblib")
    elif (MODELS_DIR / "xgb_regressor_low.joblib").exists():
        (MODELS_DIR / "xgb_regressor_low.joblib").unlink()

    if calibrator is not None:
        joblib.dump(calibrator, MODELS_DIR / "calibrator.joblib")
    elif (MODELS_DIR / "calibrator.joblib").exists():
        (MODELS_DIR / "calibrator.joblib").unlink()

    with open(MODELS_DIR / "feature_names.json", "w") as f:
        json.dump(feature_cols, f, indent=2)

    inference_cfg = {
        "cashout_threshold": clf_threshold,
        "high_risk_threshold": HIGH_RISK_THRESHOLD,
        "inference_history_steps": INFERENCE_HISTORY_STEPS,
        "low_balance_train_pct": LOW_BALANCE_TRAIN_PCT,
        "use_low_balance_model": reg_low is not None,
        "use_atm_id_enc": "atmId_enc" in feature_cols,
    }
    with open(MODELS_DIR / "inference_config.json", "w", encoding="utf-8") as f:
        json.dump(inference_cfg, f, indent=2)

    with open(MODELS_DIR / "feature_importance.json", "w", encoding="utf-8") as f:
        json.dump({"regressor": reg_imp, "classifier": clf_imp}, f, indent=2, ensure_ascii=False)

    with open(MODELS_DIR / "ab_test_atm_id.json", "w", encoding="utf-8") as f:
        json.dump(ab_report, f, indent=2, ensure_ascii=False)

    metadata = _json_safe({
        "trained_at": datetime.now(timezone.utc).isoformat(),
        "xgboost_version": xgb.__version__,
        "horizon_hours": HORIZON_HOURS,
        "inference_history_steps": INFERENCE_HISTORY_STEPS,
        "hyperparameters": best_params,
        "ab_test": ab_report,
        "low_balance_model": low_metrics,
        "thresholds": inference_cfg,
        "metrics": metrics,
        "walk_forward_monthly": walk_forward,
        "regressor_best_iteration": int(getattr(reg_model, "best_iteration", XGB_N_ESTIMATORS)),
        "classifier_best_iteration": int(getattr(clf_model, "best_iteration", XGB_N_ESTIMATORS)),
    })
    with open(MODELS_DIR / "model_metadata.json", "w", encoding="utf-8") as f:
        json.dump(metadata, f, indent=2, ensure_ascii=False)

    save_encoders(le_map)
    print(f"\n  Saved → {MODELS_DIR}/")


def main():
    print("=" * 60)
    print("  ATM Pipeline — optimized training")
    print("=" * 60)

    raw = load_data()
    raw, le_map = encode_categoricals(raw)

    use_atm_id, ab_report = ab_test_atm_id_enc(raw, le_map)
    df = make_feature_df(raw, le_map, include_atm_id_enc=use_atm_id)
    feature_cols = get_feature_names(use_atm_id)
    print(f"  Features: {len(feature_cols)} | atmId_enc={use_atm_id}")

    train, val, test = temporal_split(df)
    train = train.dropna(subset=["target_balance_24h", "target_cashout_24h"])
    val   = val.dropna(subset=["target_balance_24h", "target_cashout_24h"])

    best_params = tune_hyperparameters(train, val, feature_cols)

    reg_model, reg_metrics = train_regressor(train, val, feature_cols, best_params)
    reg_low, low_metrics = train_low_balance_regressor(train, val, feature_cols, best_params)
    clf_model, clf_metrics = train_classifier(train, val, feature_cols, best_params)

    mask_va = val["target_cashout_24h"].notna()
    prob_va = clf_model.predict_proba(val.loc[mask_va, feature_cols].fillna(0))[:, 1]
    y_va = val.loc[mask_va, "target_cashout_24h"].astype(int).values
    calibrator = fit_calibrator(prob_va, y_va)
    raw_recall = float(((prob_va >= CASHOUT_THRESHOLD).astype(int)[y_va == 1]).mean()) if (y_va == 1).any() else 0.0
    clf_threshold = CASHOUT_THRESHOLD
    if calibrator is not None:
        prob_cal = calibrator.predict(prob_va)
        clf_threshold = tune_threshold(prob_cal, y_va)
        cal_recall = float(((prob_cal >= clf_threshold).astype(int)[y_va == 1]).mean())
        if cal_recall + 0.005 < raw_recall:
            print(f"\n  Calibrator skipped (recall {cal_recall:.1%} < {raw_recall:.1%})")
            calibrator = None
            clf_threshold = CASHOUT_THRESHOLD

    reg_imp = print_feature_importance(reg_model, feature_cols, "main reg")
    clf_imp = print_feature_importance(clf_model, feature_cols, "clf")

    test_metrics = evaluate_on_test(reg_model, reg_low, clf_model, calibrator, test, feature_cols, clf_threshold)
    walk_forward = walk_forward_monthly(reg_model, reg_low, clf_model, calibrator, df, feature_cols, clf_threshold)

    all_metrics = {**reg_metrics, **clf_metrics, **test_metrics, **{f"low_{k}": v for k, v in low_metrics.items()}}
    save_artifacts(
        reg_model, reg_low, clf_model, calibrator, feature_cols, all_metrics, le_map,
        reg_imp, clf_imp, walk_forward, clf_threshold, best_params, ab_report, low_metrics,
    )
    print("\n" + "=" * 60)
    print("  Готово!")
    print("=" * 60)


if __name__ == "__main__":
    main()
