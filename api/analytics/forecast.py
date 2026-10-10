"""
Прогноз спроса на наличные (XGBoost) и рекомендации по инкассации.

Модель: почасовой спрос (сумма запрошенных снятий, включая отказы «нет наличных» —
то есть неурезанный спрос) на горизонте 1..168 ч от момента прогноза (origin).
Признаки используют только данные до origin: календарь УЗ, профиль точки
(категория, гео-скоринг), сезонные лаги ≥168 ч и уровень спроса на момент origin.

Интервал P10–P90 накопленного спроса — конформная калибровка на валидации:
эмпирические квантили отношения факт / прогноз для каждого горизонта.
Метрики считаются на последних TEST_DAYS сутках, которые модель не видела.
"""

from __future__ import annotations

import json
import logging
import sqlite3
import warnings
from datetime import datetime, timedelta
from typing import Dict, List, Optional

import numpy as np
import pandas as pd
import xgboost as xgb

from api.core.calendar_uz import is_holiday, is_post_holiday, is_pre_holiday
from .config import (
    ATM_MAX_CASH_UZS,
    BUNDLE_BILLS,
    CAL_DAYS,
    CASSETTE_CAPACITY_BILLS,
    COST_OF_CAPITAL_PCT,
    DB_PATH,
    DEFAULT_CYCLE_H,
    DEFAULT_LEAD_H,
    DENOMS,
    FORECAST_HORIZON_H,
    LEVEL_CORR_CLIP,
    LEVEL_CORR_DAYS,
    LEVEL_CORR_POWER,
    METRICS_PATH,
    MODELS_DIR,
    SAFETY_RESERVE_UZS,
    SEED,
    SERVICE_LEVEL,
    SIM_NOW,
    TEST_DAYS,
    VAL_DAYS,
    WORK_END_HOUR,
    WORK_START_HOUR,
    YOUNG_HISTORY_DAYS,
)
from .generator import _standard_load

log = logging.getLogger(__name__)

H = FORECAST_HORIZON_H
CATEGORY_CODES = {"metro": 0, "bozor": 1, "mall": 2, "office": 3, "bank_branch": 4, "residential": 5, "supermarket": 6}

FEATURES = [
    "hour", "dow", "dom", "month", "is_weekend", "is_holiday", "is_pre_holiday",
    "is_post_holiday", "is_salary", "after_salary", "days_to_salary",
    "category", "geo_score", "horizon_h",
    "lag_168", "lag_336", "how_mean_4w", "lvl_24_origin", "lvl_168_origin", "history_days",
]

FEATURE_LABELS = {
    "hour": "Час суток",
    "dow": "День недели",
    "dom": "День месяца",
    "month": "Месяц",
    "is_weekend": "Выходной",
    "is_holiday": "Праздник УЗ",
    "is_pre_holiday": "Канун праздника",
    "is_post_holiday": "День после праздника",
    "is_salary": "Зарплатный день (10/25)",
    "after_salary": "День после зарплаты",
    "days_to_salary": "Дней до зарплаты",
    "category": "Тип локации",
    "geo_score": "Гео-скоринг точки",
    "horizon_h": "Горизонт прогноза",
    "lag_168": "Спрос неделю назад в тот же час",
    "lag_336": "Спрос 2 недели назад в тот же час",
    "how_mean_4w": "Среднее за 4 недели в тот же час",
    "lvl_24_origin": "Спрос за последние 24 ч",
    "lvl_168_origin": "Спрос за последние 7 дней",
    "history_days": "Длина истории банкомата",
}


# ── Признаки ─────────────────────────────────────────────────

def _calendar_frame(ts: pd.DatetimeIndex) -> pd.DataFrame:
    days = pd.Index(ts.normalize().unique())
    flags = pd.DataFrame({
        "is_holiday": [int(is_holiday(d)) for d in days],
        "is_pre_holiday": [int(is_pre_holiday(d)) for d in days],
        "is_post_holiday": [int(is_post_holiday(d)) for d in days],
    }, index=days)
    day = ts.normalize()
    dom = ts.day.values
    to_10 = np.where(dom <= 10, 10 - dom, np.nan)
    to_25 = np.where(dom <= 25, 25 - dom, np.nan)
    to_next = np.where(dom > 25, ts.days_in_month.values - dom + 10, np.fmin(to_10, to_25))
    return pd.DataFrame({
        "hour": ts.hour.values,
        "dow": ts.dayofweek.values,
        "dom": dom,
        "month": ts.month.values,
        "is_weekend": (ts.dayofweek.values >= 5).astype(int),
        "is_holiday": flags.loc[day, "is_holiday"].values,
        "is_pre_holiday": flags.loc[day, "is_pre_holiday"].values,
        "is_post_holiday": flags.loc[day, "is_post_holiday"].values,
        "is_salary": np.isin(dom, (10, 25)).astype(int),
        "after_salary": np.isin(dom, (11, 26)).astype(int),
        "days_to_salary": to_next,
    })


class Series:
    """Непрерывный почасовой ряд спроса одного банкомата (NaN — нет связи)."""

    def __init__(self, atm: dict, frame: pd.DataFrame):
        self.atm = atm
        # Метка часа = начало корзины [t, t+1h); последняя корзина заканчивается в SIM_NOW
        idx = pd.date_range(frame.index.min(), pd.Timestamp(SIM_NOW) - pd.Timedelta(hours=1), freq="h")
        f = frame.reindex(idx)
        y = f["demand_amount"].astype(float).where(f["online"] == 1)
        self.index = idx
        self.y = y.values
        s = pd.Series(self.y, index=idx)
        self.r24 = s.rolling(24, min_periods=12).mean().values
        self.r168 = s.rolling(168, min_periods=48).mean().values
        self.start = idx[0]

    def _lag(self, pos: np.ndarray, lag: int) -> np.ndarray:
        p = pos - lag
        out = np.full(len(pos), np.nan)
        ok = p >= 0
        out[ok] = self.y[p[ok]]
        return out

    def features(self, target_pos: np.ndarray, horizon: np.ndarray, target_ts: pd.DatetimeIndex) -> pd.DataFrame:
        """target_pos может выходить за конец ряда (прогноз в будущее)."""
        origin = target_pos - horizon
        cal = _calendar_frame(target_ts)
        lags = [self._lag(target_pos, 168 * k) for k in (1, 2, 3, 4)]
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", category=RuntimeWarning)
            how = np.nanmean(np.vstack(lags), axis=0)
        ok = origin >= 0
        lvl24 = np.full(len(origin), np.nan)
        lvl168 = np.full(len(origin), np.nan)
        lvl24[ok] = self.r24[origin[ok]]
        lvl168[ok] = self.r168[origin[ok]]
        cal["category"] = CATEGORY_CODES[self.atm["category"]]
        cal["geo_score"] = self.atm["geo_score"]
        cal["horizon_h"] = horizon
        cal["lag_168"] = lags[0]
        cal["lag_336"] = lags[1]
        cal["how_mean_4w"] = how
        cal["lvl_24_origin"] = lvl24 * 24
        cal["lvl_168_origin"] = lvl168 * 168
        cal["history_days"] = np.maximum(origin, 0) / 24.0
        return cal[FEATURES]


def load_series(conn: sqlite3.Connection) -> Dict[str, Series]:
    atms = pd.read_sql("SELECT * FROM atms", conn).to_dict("records")
    hourly = pd.read_sql(
        "SELECT atm_id, ts, online, demand_amount FROM hourly", conn, parse_dates=["ts"]
    )
    out = {}
    for atm in atms:
        f = hourly[hourly["atm_id"] == atm["atm_id"]].set_index("ts")
        if len(f):
            out[atm["atm_id"]] = Series(atm, f)
    return out


def level_factor(model, s: Series, o_pos: int) -> float:
    """Поправка уровня для молодых банкоматов (история < YOUNG_HISTORY_DAYS).

    Пока не накопилось 4 недели, признак «среднее за 4 недели» строится по
    периоду разгона и занижает спрос. Сравниваем факт последних LEVEL_CORR_DAYS
    суток с прогнозом модели на те же часы (горизонт 24 ч) и масштабируем
    прогноз на корень из этого отношения. Использует только данные до origin."""
    if (o_pos + 1) / 24 >= YOUNG_HISTORY_DAYS:
        return 1.0
    pos = np.arange(max(o_pos - 24 * LEVEL_CORR_DAYS + 1, 0), o_pos + 1)
    hz = np.full(len(pos), 24)
    ok = pos - hz >= 0
    pos, hz = pos[ok], hz[ok]
    if len(pos) < 48:
        return 1.0
    actual = s.y[pos]
    good = ~np.isnan(actual)
    if good.sum() < 48:
        return 1.0
    pred = np.maximum(model.predict(s.features(pos, hz, s.index[pos])), 0)
    ratio = actual[good].sum() / max(pred[good].sum(), 1.0)
    lo, hi = LEVEL_CORR_CLIP
    return float(np.clip(ratio, lo, hi) ** LEVEL_CORR_POWER)


# ── Обучение и оценка ───────────────────────────────────────

def _training_rows(series: Dict[str, Series], t_from: datetime, t_to: datetime, rng) -> pd.DataFrame:
    """Одна строка на целевой час, горизонт выбирается случайно из 1..H."""
    frames = []
    for atm_id, s in series.items():
        mask = (s.index >= t_from) & (s.index < t_to) & ~np.isnan(s.y)
        pos = np.nonzero(mask)[0]
        if not len(pos):
            continue
        hz = rng.integers(1, H + 1, size=len(pos))
        X = s.features(pos, hz, s.index[pos])
        X["y"] = s.y[pos]
        X["atm_id"] = atm_id
        frames.append(X)
    return pd.concat(frames, ignore_index=True)


def _origin_paths(model, series: Dict[str, Series], t_from: datetime, t_to: datetime) -> List[dict]:
    """Прогнозы с ежедневных origin в 18:00 на 1..H ч — как в реальном планировании."""
    paths = []
    for atm_id, s in series.items():
        o = pd.Timestamp(t_from).normalize() + pd.Timedelta(hours=18)
        while o + pd.Timedelta(hours=H) <= pd.Timestamp(t_to):
            if o >= s.start + pd.Timedelta(days=2):
                # последняя закрытая корзина [o-1h, o); цели — корзины o .. o+H-1
                o_pos = int((o - s.start) / pd.Timedelta(hours=1)) - 1
                hz = np.arange(1, H + 1)
                pos = o_pos + hz
                ts = s.index[pos]
                pred = np.maximum(model.predict(s.features(pos, hz, ts)), 0)
                pred = pred * level_factor(model, s, o_pos)
                actual = s.y[pos]
                naive = s._lag(pos, 168)
                weekly_avg = np.full(H, s.r168[o_pos])
                paths.append({
                    "atm_id": atm_id, "origin": o, "pred": pred, "actual": actual,
                    "naive": naive, "bank": weekly_avg,
                })
            o += pd.Timedelta(days=1)
    return paths


def _wape(pred_sum: np.ndarray, act_sum: np.ndarray) -> float:
    ok = ~np.isnan(pred_sum) & ~np.isnan(act_sum)
    return float(np.abs(pred_sum[ok] - act_sum[ok]).sum() / max(act_sum[ok].sum(), 1) * 100)


def _cum_sums(paths: List[dict], key: str, hours: int) -> np.ndarray:
    out = []
    for p in paths:
        a = p["actual"][:hours]
        v = p[key][:hours]
        if np.isnan(a).any():
            out.append(np.nan)
            continue
        out.append(np.nansum(v) if not np.isnan(v).all() else np.nan)
    return np.array(out, dtype=float)


def _policy_backtest(conn: sqlite3.Connection, paths: List[dict], safe: np.ndarray,
                     hi: np.ndarray, cycle_h: int = DEFAULT_CYCLE_H,
                     lead_h: int = DEFAULT_LEAD_H) -> dict:
    """Проверка политики загрузки на тестовых origin.

    Для каждого origin: загрузка = прогноз за цикл × верхняя граница + резерв;
    срок визита = момент, когда верхняя граница накопленного спроса съедает
    остаток (минус резерв), минус lead_h. Сравнивается с фактическим спросом.
    Рабочее окно инкассации не учитывается — реальный выезд только раньше,
    поэтому доля «опоздавших визитов» здесь оценка сверху."""
    if not paths:
        return {}
    since = (min(p["origin"] for p in paths) - pd.Timedelta(hours=1)).isoformat(sep=" ")
    bal = pd.read_sql_query("SELECT atm_id, ts, balance FROM hourly WHERE ts>=?", conn, params=(since,))
    bal_at = {(a, t): b for a, t, b in bal.itertuples(index=False)}
    denoms = np.array(DENOMS)
    std_value = {
        a: float((_standard_load(g) * denoms).sum())
        for a, g in conn.execute("SELECT atm_id, geo_score FROM atms")
    }
    k = min(cycle_h, H) - 1
    policies = {"ml_service_level": safe, "ml_p90": hi}
    stats = {name: {"load": [], "short": [], "miss": []} for name in policies}
    std_load, std_short = [], []
    for p in paths:
        actual = p["actual"]
        if np.isnan(actual[: k + 1]).any():
            continue
        b = bal_at.get((p["atm_id"], (p["origin"] - pd.Timedelta(hours=1)).isoformat(sep=" ")))
        if b is None:
            continue
        c50 = np.cumsum(p["pred"])
        ca = np.cumsum(np.nan_to_num(actual))
        for name, up in policies.items():
            load = min(c50[k] * up[k] + SAFETY_RESERVE_UZS, ATM_MAX_CASH_UZS)
            stats[name]["load"].append(load)
            stats[name]["short"].append(ca[k] > load)
            i_crit = _first_cross(c50 * up, max(b - SAFETY_RESERVE_UZS, 0))
            visit = H if i_crit is None else max(i_crit - lead_h, 0)
            stats[name]["miss"].append(bool((ca[:visit] >= b).any()))
        std = std_value.get(p["atm_id"], 0.0)
        std_load.append(std)
        std_short.append(ca[k] > std)

    out = {"cycle_h": cycle_h, "n_paths": len(std_load), "policies": {}}
    for name, st in stats.items():
        if not st["load"]:
            continue
        out["policies"][name] = {
            "avg_load_uzs": int(np.mean(st["load"])),
            "cycle_shortfall_pct": round(float(np.mean(st["short"]) * 100), 2),
            "cashout_before_visit_pct": round(float(np.mean(st["miss"]) * 100), 2),
        }
    if std_load:
        out["policies"]["bank_standard"] = {
            "avg_load_uzs": int(np.mean(std_load)),
            "cycle_shortfall_pct": round(float(np.mean(std_short) * 100), 2),
            "cashout_before_visit_pct": None,
        }
    return out


def train_and_evaluate(conn: sqlite3.Connection) -> dict:
    MODELS_DIR.mkdir(parents=True, exist_ok=True)
    rng = np.random.default_rng(SEED)
    series = load_series(conn)

    # train | validation (ранняя остановка) | calibration (интервалы) | test
    test_from = SIM_NOW - timedelta(days=TEST_DAYS)
    cal_from = test_from - timedelta(days=CAL_DAYS)
    val_from = cal_from - timedelta(days=VAL_DAYS)
    first = min(s.start for s in series.values()).to_pydatetime()

    train = _training_rows(series, first + timedelta(days=7), val_from, rng)
    val = _training_rows(series, val_from, cal_from, rng)
    log.info("Train rows: %d | val rows: %d", len(train), len(val))

    model = xgb.XGBRegressor(
        objective="reg:tweedie", tweedie_variance_power=1.3,
        n_estimators=1200, learning_rate=0.05, max_depth=7,
        subsample=0.8, colsample_bytree=0.8, min_child_weight=10,
        tree_method="hist", random_state=SEED, n_jobs=-1,
        early_stopping_rounds=60, eval_metric="mae",
    )
    model.fit(train[FEATURES], train["y"], eval_set=[(val[FEATURES], val["y"])], verbose=False)
    model.save_model(str(MODELS_DIR / "demand_xgb.json"))

    # Конформная калибровка на отдельном окне, которое не участвовало ни в обучении,
    # ни в ранней остановке; origin берутся так, чтобы весь горизонт попадал в окно.
    cal_paths = _origin_paths(model, series, cal_from - timedelta(hours=H), test_from)
    ratios = []
    for p in cal_paths:
        if np.isnan(p["actual"]).any():
            continue
        cp = np.cumsum(p["pred"])
        ca = np.cumsum(p["actual"])
        ratios.append(ca / np.maximum(cp, 1))
    ratios = np.array(ratios)
    qs = np.round(np.arange(0.02, 0.99, 0.02), 2)
    band = {
        "lo": np.quantile(ratios, 0.10, axis=0).tolist(),
        "hi": np.quantile(ratios, 0.90, axis=0).tolist(),
        # верхняя граница уровня сервиса: по ней планируются срок визита и загрузка
        "safe": np.quantile(ratios, SERVICE_LEVEL, axis=0).tolist(),
        "service_level": SERVICE_LEVEL,
        # распределение «факт / прогноз» накопленного спроса за 24 ч — для P(cash-out за 24 ч)
        "ratio24_q": qs.tolist(),
        "ratio24_v": np.quantile(ratios[:, 23], qs).tolist(),
        "n_paths": int(len(ratios)),
    }
    (MODELS_DIR / "band.json").write_text(json.dumps(band))

    # Тест: модель против бейзлайнов на тех же origin
    test_paths = _origin_paths(model, series, test_from, SIM_NOW)
    metrics_h = {}
    for hours, label in ((24, "24h"), (72, "72h"), (168, "7d")):
        act = _cum_sums(test_paths, "actual", hours)
        row = {}
        for key, name in (("pred", "model"), ("naive", "seasonal_naive"), ("bank", "bank_weekly_avg")):
            row[name] = round(_wape(_cum_sums(test_paths, key, hours), act), 2)
        metrics_h[label] = row

    lo = np.array(band["lo"])
    hi = np.array(band["hi"])
    inside = []
    for p in test_paths:
        if np.isnan(p["actual"]).any():
            continue
        cp, ca = np.cumsum(p["pred"]), np.cumsum(p["actual"])
        inside.append(((ca >= cp * lo) & (ca <= cp * hi)).mean())
    coverage = float(np.mean(inside) * 100) if inside else float("nan")

    policy = _policy_backtest(conn, test_paths, np.array(band["safe"]), hi)

    # Холодный старт: банкоматы с историей < YOUNG_HISTORY_DAYS — отдельно
    pilot_ids = {a for a, s in series.items()
                 if (SIM_NOW - s.start.to_pydatetime()).days < YOUNG_HISTORY_DAYS}
    pilot_paths = [p for p in test_paths if p["atm_id"] in pilot_ids]
    cold = None
    if pilot_paths:
        act = _cum_sums(pilot_paths, "actual", 24)
        cold = {
            "model": round(_wape(_cum_sums(pilot_paths, "pred", 24), act), 2),
            "seasonal_naive": round(_wape(_cum_sums(pilot_paths, "naive", 24), act), 2),
            "n_paths": len(pilot_paths),
        }

    imp = model.get_booster().get_score(importance_type="gain")
    total_gain = sum(imp.values()) or 1
    importance = sorted(
        ({"feature": k, "label": FEATURE_LABELS.get(k, k), "share": round(v / total_gain * 100, 2)}
         for k, v in imp.items()),
        key=lambda x: -x["share"],
    )

    metrics = {
        "trained_at": datetime.utcnow().isoformat(timespec="seconds") + "Z",
        "data": "synthetic (api/analytics/generator.py)",
        "split": {
            "train": f"{first.date()} — {val_from.date()}",
            "validation": f"{val_from.date()} — {cal_from.date()}",
            "calibration": f"{cal_from.date()} — {test_from.date()}",
            "test": f"{test_from.date()} — {SIM_NOW.date()}",
        },
        "rows": {"train": int(len(train)), "validation": int(len(val))},
        "best_iteration": int(model.best_iteration),
        "wape_pct": metrics_h,
        "interval_p10_p90_coverage_pct": round(coverage, 1),
        "service_level": SERVICE_LEVEL,
        "load_policy_backtest": policy,
        "calibration_paths": int(len(ratios)),
        "test_origins": len(test_paths),
        "cold_start_24h": cold,
        "feature_importance": importance[:12],
        "xgboost_version": xgb.__version__,
    }
    METRICS_PATH.write_text(json.dumps(metrics, ensure_ascii=False, indent=2))
    return metrics


# ── Инференс и рекомендации ──────────────────────────────────

class Forecaster:
    def __init__(self, conn: sqlite3.Connection):
        self.model = xgb.XGBRegressor()
        self.model.load_model(str(MODELS_DIR / "demand_xgb.json"))
        band = json.loads((MODELS_DIR / "band.json").read_text())
        self.lo = np.array(band["lo"])
        self.hi = np.array(band["hi"])
        # старый band.json без "safe" — откат на P90
        self.safe = np.array(band.get("safe", band["hi"]))
        self.service_level = float(band.get("service_level", 0.9) if "safe" in band else 0.9)
        self.ratio24_q = np.array(band.get("ratio24_q", [0.1, 0.9]))
        self.ratio24_v = np.array(band.get("ratio24_v", [band["lo"][23], band["hi"][23]]))
        self.series = load_series(conn)
        self.now = pd.Timestamp(SIM_NOW)

    def forecast(self, atm_id: str) -> Optional[dict]:
        s = self.series.get(atm_id)
        if s is None:
            return None
        o_pos = len(s.index) - 1
        hz = np.arange(1, H + 1)
        ts = pd.date_range(self.now, periods=H, freq="h")
        X = s.features(o_pos + hz, hz, ts)
        factor = level_factor(self.model, s, o_pos)
        pred = np.maximum(self.model.predict(X), 0) * factor
        contribs = self.model.get_booster().predict(xgb.DMatrix(X.iloc[:24]), pred_contribs=True)
        return {"ts": ts, "pred": pred, "X": X, "contribs": contribs, "level_factor": factor}

    def cashout_prob_24h(self, fc: dict, balance: float) -> float:
        """P(спрос за 24 ч > остатка) по калиброванному распределению ошибки модели."""
        expected = float(np.sum(fc["pred"][:24]))
        if expected <= 0:
            return 0.0
        threshold = balance / expected
        # за пределами сетки квантилей (2–98%) оценка не точнее этих границ
        cdf = float(np.interp(threshold, self.ratio24_v, self.ratio24_q,
                              left=self.ratio24_q[0], right=self.ratio24_q[-1]))
        return round(1.0 - cdf, 3)

    def explain(self, fc: dict) -> List[dict]:
        """Вклад признаков в прогноз ближайших 24 ч (SHAP, в лог-шкале модели)."""
        c = fc["contribs"]
        pred24 = fc["pred"][:24]
        weights = pred24 / max(pred24.sum(), 1)
        shares = (c[:, :-1] * weights[:, None]).sum(axis=0)
        total_abs = np.abs(shares).sum() or 1
        out = [
            {"feature": f, "label": FEATURE_LABELS[f], "impact_pct": round(float(v / total_abs * 100), 1)}
            for f, v in zip(FEATURES, shares)
        ]
        out.sort(key=lambda x: -abs(x["impact_pct"]))
        return out[:6]


def _snap_to_window(t: pd.Timestamp, now: pd.Timestamp) -> pd.Timestamp:
    """Последний момент рабочего окна инкассации (Пн–Сб 09–18) не позже t."""
    cur = t.floor("h")
    while cur > now:
        if cur.dayofweek != 6 and WORK_START_HOUR <= cur.hour < WORK_END_HOUR:
            return cur
        cur -= pd.Timedelta(hours=1)
    return now


def _hours_to_next_window(now: pd.Timestamp) -> int:
    """Через сколько часов экипаж сможет выехать (0 — окно Пн–Сб 09–18 открыто сейчас)."""
    cur = now.floor("h")
    h = 0
    while not (cur.dayofweek != 6 and WORK_START_HOUR <= cur.hour < WORK_END_HOUR):
        cur += pd.Timedelta(hours=1)
        h += 1
    return h


def _first_cross(cum: np.ndarray, threshold: float) -> Optional[int]:
    idx = np.nonzero(cum >= threshold)[0]
    return int(idx[0]) if len(idx) else None


def allocate_bills(need: float, denom_shares: List[float]) -> List[int]:
    """Раскладка суммы по кассетам пропорционально выдаче номиналов.

    Кассета, упёршаяся в ёмкость, фиксируется на максимуме, а недостающая
    сумма перераспределяется по остальным (water-filling). Каждая кассета
    округляется вверх до корешка; сумма по всем кассетам не превышает
    ATM_MAX_CASH_UZS — если округление вверх выходит за предел, кассеты
    округляются вниз и добиваются корешками, пока помещаются."""
    need = min(need, ATM_MAX_CASH_UZS)
    denoms = np.array(DENOMS, dtype=float)
    w = np.maximum(np.asarray(denom_shares, dtype=float), 0.02)
    w = w / w.sum()
    caps = CASSETTE_CAPACITY_BILLS * denoms
    alloc = np.zeros(len(DENOMS))
    free = np.ones(len(DENOMS), dtype=bool)
    while free.any():
        rem = need - alloc.sum()
        if rem <= 0:
            break
        new = alloc + rem * np.where(free, w, 0) / w[free].sum()
        over = free & (new >= caps)
        alloc = np.where(over, caps, np.where(free, new, alloc))
        free &= ~over
        if not over.any():
            break
    bills = np.minimum(np.ceil(alloc / denoms / BUNDLE_BILLS) * BUNDLE_BILLS, CASSETTE_CAPACITY_BILLS)
    if (bills * denoms).sum() > ATM_MAX_CASH_UZS:
        bills = np.minimum(np.floor(alloc / denoms / BUNDLE_BILLS) * BUNDLE_BILLS, CASSETTE_CAPACITY_BILLS)
        for j in np.argsort(-w):
            while (
                (bills * denoms).sum() < need
                and bills[j] + BUNDLE_BILLS <= CASSETTE_CAPACITY_BILLS
                and (bills * denoms).sum() + BUNDLE_BILLS * denoms[j] <= ATM_MAX_CASH_UZS
            ):
                bills[j] += BUNDLE_BILLS
    return [int(b) for b in bills]


def recommend(
    fc: dict,
    balance: int,
    cassettes: List[int],
    denom_shares: List[float],
    lo: np.ndarray,
    hi: np.ndarray,
    now: pd.Timestamp,
    standard_load_value: int,
    cycle_h: int = DEFAULT_CYCLE_H,
    lead_h: int = DEFAULT_LEAD_H,
    rate_pct: float = COST_OF_CAPITAL_PCT,
    safe: Optional[np.ndarray] = None,
    service_level: float = SERVICE_LEVEL,
) -> dict:
    if safe is None:
        safe, service_level = hi, 0.9
    ts = fc["ts"]
    pred = fc["pred"]
    c50 = np.cumsum(pred)
    c_hi = c50 * hi
    c_lo = c50 * lo
    c_safe = c50 * safe

    def at(i: Optional[int]) -> Optional[str]:
        # событие внутри корзины [ts, ts+1h) — показываем конец корзины
        return (ts[i] + pd.Timedelta(hours=1)).isoformat(sep=" ") if i is not None else None

    practically_empty = max(balance - 2_000_000, 0)
    i_mid = _first_cross(c50, practically_empty)
    i_early = _first_cross(c_hi, practically_empty)
    i_late = _first_cross(c_lo, practically_empty)

    # Первая кассета, которая закончится (по структуре выдачи номиналов).
    # Уже пустая кассета (при ненулевой доле номинала в выдаче) — первая.
    first_cassette = None
    best_k = None
    for j, d in enumerate(DENOMS):
        value = cassettes[j] * d
        share = denom_shares[j]
        if share <= 0.005:
            continue
        k = -1 if value <= 0 else _first_cross(c50 * share, value)
        if k is not None and (best_k is None or k < best_k):
            best_k = k
            first_cassette = {"denom": d, "at": now.isoformat(sep=" ") if k < 0 else at(k), "already_empty": k < 0}

    # Крайний срок пополнения: когда верхняя граница уровня сервиса съест остаток до резерва
    i_crit = _first_cross(c_safe, max(balance - SAFETY_RESERVE_UZS, 0))
    if i_crit is None:
        visit = None
        urgency = "ok"
    else:
        deadline = ts[i_crit] - pd.Timedelta(hours=lead_h)
        visit = _snap_to_window(deadline, now)
        hours_left = (visit - now) / pd.Timedelta(hours=1)
        urgency = "urgent" if hours_left <= 6 else "today" if visit.date() == now.date() or hours_left <= 24 else "planned"

    # Сумма загрузки: покрыть спрос за цикл после визита на уровне сервиса + резерв
    v_off = 0 if visit is None else max(int((visit - now) / pd.Timedelta(hours=1)), 0)
    base = c50[v_off - 1] if v_off > 0 else 0.0
    balance_at_visit = int(min(max(balance - base, 0), ATM_MAX_CASH_UZS))
    end = v_off + cycle_h
    if end <= H:
        cycle_mean = c50[end - 1] - base
    else:
        daily_tail = pred[-24:].sum()
        cycle_mean = c50[-1] - base + daily_tail * (end - H) / 24
    k = min(cycle_h, H) - 1
    cycle_safe = cycle_mean * safe[k]
    need = cycle_safe + SAFETY_RESERVE_UZS

    bills = allocate_bills(need, denom_shares)
    rec_value = int(sum(b * d for b, d in zip(bills, DENOMS)))
    capacity_limited = rec_value < need
    covers_h = cycle_h
    if capacity_limited:
        # сколько часов после визита загрузка покрывает на уровне сервиса
        hs = np.arange(1, H - v_off + 1)
        dem = (c50[v_off + hs - 1] - base) * safe[hs - 1]
        over = np.nonzero(dem > rec_value - SAFETY_RESERVE_UZS)[0]
        covers_h = int(over[0]) if len(over) else int(len(hs))

    # Замороженные деньги: средний неиспользуемый остаток за цикл, ML vs стандартная загрузка
    avg_use = cycle_mean / 2
    idle_ml = max(rec_value - avg_use, 0)
    idle_std = max(standard_load_value - avg_use, 0)
    freed = max(idle_std - idle_ml, 0)
    cost_saved = freed * rate_pct / 100 * cycle_h / 8760

    return {
        "empty_at": at(i_mid),
        "empty_window": {"earliest": at(i_early), "latest": at(i_late)},
        "hours_to_empty": (i_mid + 1) if i_mid is not None else None,
        "first_cassette_empty": first_cassette,
        "urgency": urgency,
        "visit_by": visit.isoformat(sep=" ") if visit is not None else None,
        "next_window_h": _hours_to_next_window(now),
        "next_window_at": (now.floor("h") + pd.Timedelta(hours=_hours_to_next_window(now))).isoformat(sep=" "),
        "cycle_h": cycle_h,
        "demand_cycle_expected": int(cycle_mean),
        "demand_cycle_safe": int(cycle_safe),
        "service_level": service_level,
        "load": {"bills": bills, "value": rec_value},
        # к визиту в банкомате останется balance_at_visit; довезти нужно только разницу
        # до рекомендованного уровня — сумма в банкомате после визита ≤ ATM_MAX_CASH_UZS
        "balance_at_visit": balance_at_visit,
        "topup_value": max(rec_value - balance_at_visit, 0),
        "capacity_limited": bool(capacity_limited),
        "covers_h": covers_h,
        "standard_load_value": standard_load_value,
        "freed_cash": int(freed),
        "cost_saved_per_cycle": int(cost_saved),
        "rate_pct": rate_pct,
        "demand_24h": {
            "expected": int(c50[23]), "p10": int(c_lo[23]), "p90": int(c_hi[23]),
        },
        "balance_path": {
            "ts": [t.isoformat(sep=" ") for t in ts],
            "p50": [int(max(balance - x, 0)) for x in c50],
            "p10": [int(max(balance - x, 0)) for x in c_hi],
            "p90": [int(max(balance - x, 0)) for x in c_lo],
        },
    }
