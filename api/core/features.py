"""
Единая функция построения признаков.
Используется и при обучении (train_model.py) и при live-инференсе (predictor.py).
"""

from __future__ import annotations

from typing import List

import numpy as np
import pandas as pd

from .calendar_uz import is_salary_day, is_near_salary, is_holiday, season_factor
from .config import GEO_LAT_CENTER, GEO_LON_CENTER

LAG_STEPS    = [1, 3, 6, 12, 24]
ROLL_WINDOWS = [6, 12, 24]

TX_COUNT_COLS = [
    "numberIncomeTransaction",
    "numberOutcomeTransaction",
    "totalNumberTransaction",
]


def _ensure_tx_columns(df: pd.DataFrame) -> pd.DataFrame:
    for col in TX_COUNT_COLS[:2]:
        if col not in df.columns:
            df[col] = 0
    if "totalNumberTransaction" not in df.columns:
        df["totalNumberTransaction"] = (
            df["numberIncomeTransaction"] + df["numberOutcomeTransaction"]
        )
    return df


def make_features(
    df: pd.DataFrame,
    capacity: int,
    profile_enc: int = 0,
    atm_id_enc: int = 0,
    include_atm_id_enc: bool = True,
    lat: float | None = None,
    lon: float | None = None,
) -> pd.DataFrame:
    df = df.copy()
    df["transactionTime"] = pd.to_datetime(df["transactionTime"])
    df = df.sort_values("transactionTime").reset_index(drop=True)
    df = _ensure_tx_columns(df)

    cap = capacity or 1

    df["totalBalance_pct"] = df["totalBalance"] / cap
    df["net_cash_flow"]    = df["totalIncome"] - df["totalOutcome"]

    dt = df["transactionTime"]
    df["hour"]             = dt.dt.hour
    df["day_of_week"]      = dt.dt.dayofweek
    df["week_of_year"]     = dt.dt.isocalendar().week.astype(int)
    df["month"]            = dt.dt.month
    df["is_weekend"]       = (df["day_of_week"] >= 5).astype(int)
    df["is_holiday"]       = dt.apply(lambda x: int(is_holiday(x))).astype(int)
    df["is_pre_holiday"]   = dt.shift(-1).apply(
        lambda x: int(is_holiday(x)) if pd.notna(x) else 0
    ).astype(int)
    df["is_post_holiday"]  = dt.shift(1).apply(
        lambda x: int(is_holiday(x)) if pd.notna(x) else 0
    ).astype(int)
    df["is_non_working_day"] = ((df["is_weekend"] == 1) | (df["is_holiday"] == 1)).astype(int)
    df["is_salary_day"]    = dt.dt.day.map(is_salary_day).astype(int)
    df["is_near_salary"]   = dt.dt.day.map(is_near_salary).astype(int)
    df["season_factor"]    = dt.dt.month.map(season_factor)
    df["atm_capacity"]     = cap
    df["atmProfile_enc"]   = profile_enc

    if lat is not None and lon is not None:
        df["lat_norm"] = float(lat) - GEO_LAT_CENTER
        df["lon_norm"] = float(lon) - GEO_LON_CENTER
    elif "lat" in df.columns and "lon" in df.columns:
        df["lat_norm"] = df["lat"].astype(float) - GEO_LAT_CENTER
        df["lon_norm"] = df["lon"].astype(float) - GEO_LON_CENTER
    else:
        df["lat_norm"] = 0.0
        df["lon_norm"] = 0.0

    if include_atm_id_enc:
        df["atmId_enc"] = atm_id_enc

    for lag in LAG_STEPS:
        df[f"bal_lag_{lag}"]  = df["totalBalance"].shift(lag)
        df[f"out_lag_{lag}"]  = df["totalOutcome"].shift(lag)
        df[f"pct_lag_{lag}"]  = df["totalBalance_pct"].shift(lag)

    for w in ROLL_WINDOWS:
        rolled_bal = df["totalBalance"].shift(1).rolling(w)
        rolled_out = df["totalOutcome"].shift(1).rolling(w)
        df[f"bal_roll_mean_{w}"] = rolled_bal.mean()
        df[f"bal_roll_std_{w}"]  = rolled_bal.std().fillna(0)
        df[f"bal_roll_min_{w}"]  = rolled_bal.min()
        df[f"out_roll_mean_{w}"] = rolled_out.mean()
        df[f"out_roll_sum_{w}"]  = rolled_out.sum()

    avg_burn = df["totalOutcome"].shift(1).rolling(6).mean()
    df["burn_rate_6p"]  = avg_burn
    df["time_to_empty"] = (
        df["totalBalance"] / avg_burn.replace(0, np.nan)
    ).clip(upper=200).fillna(200)

    df["pct_change_6p"] = df["totalBalance_pct"].pct_change(periods=6).fillna(0).clip(-1, 1)

    if "is_incassation" in df.columns:
        inc = df["is_incassation"].astype(float)
        df["inc_last_12p"] = inc.shift(1).rolling(12).sum().fillna(0)
        df["inc_last_24p"] = inc.shift(1).rolling(24).sum().fillna(0)
    else:
        df["inc_last_12p"] = 0
        df["inc_last_24p"] = 0

    return df


def get_feature_names(include_atm_id_enc: bool = True) -> List[str]:
    base = [
        "numberIncomeTransaction", "numberOutcomeTransaction",
        "totalIncome", "totalOutcome", "totalNumberTransaction",
        "net_cash_flow",
        "hour", "day_of_week", "week_of_year", "month",
        "is_weekend", "is_holiday", "is_pre_holiday", "is_post_holiday",
        "is_non_working_day", "is_salary_day", "is_near_salary",
        "season_factor", "atm_capacity",
    ]
    lags = [
        f"{p}_lag_{lag}"
        for lag in LAG_STEPS
        for p in ["bal", "out", "pct"]
    ]
    rolls = [
        f"{p}_{stat}_{w}"
        for w in ROLL_WINDOWS
        for p, stat in [
            ("bal", "roll_mean"), ("bal", "roll_std"), ("bal", "roll_min"),
            ("out", "roll_mean"), ("out", "roll_sum"),
        ]
    ]
    derived = [
        "burn_rate_6p", "time_to_empty", "pct_change_6p",
        "inc_last_12p", "inc_last_24p",
        "atmProfile_enc", "lat_norm", "lon_norm",
    ]
    if include_atm_id_enc:
        derived.append("atmId_enc")
    return base + lags + rolls + derived
