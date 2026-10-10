"""
Экспортирует лёгкие JSON-файлы для дашборда вместо полного CSV.
Запускать после generate_atm_dataset.py и train_model.py.

Создаёт:
  dashboard/data/timeseries.json  — последние N дней по каждому ATM
  dashboard/data/predictions.json — ML-прогнозы
  dashboard/data/meta.json        — метаданные ATM
"""

import json
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "api"))
from core.config import CSV_PATH, PRED_PATH, BASE_DIR  # noqa: E402

OUT_DIR   = BASE_DIR / "dashboard" / "data"
DAYS_BACK = 30

OUT_DIR.mkdir(parents=True, exist_ok=True)

print("Читаем CSV...")
df = pd.read_csv(CSV_PATH, parse_dates=["transactionTime"])
df = df.sort_values(["atmId", "transactionTime"])

cutoff = df["transactionTime"].max() - pd.Timedelta(days=DAYS_BACK)
df = df[df["transactionTime"] >= cutoff]
print(f"  Строк за последние {DAYS_BACK} дней: {len(df):,}")

# ── 1. META — статические данные по каждому ATM ──────────────
meta_cols = ["atmId", "atmName", "atmBank", "atmProfile",
             "atmAddress", "lat", "lon", "atm_capacity"]
meta_cols = [c for c in meta_cols if c in df.columns]
meta = (df[meta_cols].drop_duplicates("atmId")
                      .set_index("atmId")
                      .to_dict(orient="index"))

with open(OUT_DIR / "meta.json", "w", encoding="utf-8") as f:
    json.dump(meta, f, ensure_ascii=False)
print(f"  meta.json: {len(meta)} ATM")

# ── 2. TIMESERIES — история баланса для графиков ─────────────
ts_cols = [
    "atmId", "transactionTime",
    "totalBalance", "atm_capacity",
    "totalOutcome", "is_incassation",
    "low_cash_alert",
]
ts_cols = [c for c in ts_cols if c in df.columns]

timeseries = {}
for atm_id, grp in df[ts_cols].groupby("atmId"):
    grp = grp.copy()
    grp["transactionTime"] = grp["transactionTime"].dt.strftime("%Y-%m-%dT%H:%M:%S")
    timeseries[atm_id] = grp.drop(columns="atmId").to_dict(orient="records")

with open(OUT_DIR / "timeseries.json", "w", encoding="utf-8") as f:
    json.dump(timeseries, f, ensure_ascii=False, separators=(",", ":"))

size_kb = (OUT_DIR / "timeseries.json").stat().st_size / 1024
print(f"  timeseries.json: {size_kb:.0f} KB ({DAYS_BACK} дней)")

# ── 3. PREDICTIONS — ML-прогнозы ─────────────────────────────
pred_cols = [
    "atmId", "totalBalance", "cash_utilization_pct",
    "pred_balance_24h", "pred_balance_pct_24h",
    "pred_cashout_prob", "pred_cashout_risk", "risk_label",
    "transactionTime",
]
pred_df = pd.read_csv(PRED_PATH)
pred_cols_exist = [c for c in pred_cols if c in pred_df.columns]
preds = pred_df[pred_cols_exist].set_index("atmId").to_dict(orient="index")

with open(OUT_DIR / "predictions.json", "w", encoding="utf-8") as f:
    json.dump(preds, f, ensure_ascii=False, separators=(",", ":"))
print(f"  predictions.json: {len(preds)} ATM")

print("\nГотово! Файлы в dashboard/data/")
