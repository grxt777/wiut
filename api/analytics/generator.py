"""
Генератор синтетических данных банкоматов (метод задекларирован в README).

Уровень транзакций:
  - интенсивность снятий в час = базовый спрос точки (от гео-скоринга)
    × профиль часа и дня недели категории × зарплатные дни × праздники УЗ
    × сезон × латентный дневной шум AR(1) × редкие всплески;
  - каждая транзакция выдаёт конкретные купюры из 4 кассет (10k/50k/100k/200k);
  - если купюр не хватает — отказ «нет наличных» (реальный cash-out в данных).

Инкассация воспроизводит текущую практику банка (baseline):
  визит по расписанию раз в K дней + аварийный выезд с задержкой
  при остатке ниже порога; загрузка стандартным комплектом.

Номера карт: только маска (BIN платёжной системы + последние 4 цифры) и
необратимый токен. Полный номер карты не генерируется и не хранится.
"""

from __future__ import annotations

import hashlib
import json
import logging
import sqlite3
from datetime import date, datetime, timedelta
from typing import Dict, List, Tuple

import numpy as np

from api.core.calendar_uz import is_holiday, is_post_holiday, is_pre_holiday, is_salary_day
from .config import (
    ATM_MAX_CASH_UZS,
    BUNDLE_BILLS,
    CASSETTE_CAPACITY_BILLS,
    DB_PATH,
    DENOMS,
    EMERGENCY_DELAY_H,
    EMERGENCY_PCT,
    SEED,
    SIM_NOW,
    SIM_START,
    STANDARD_LOAD_BILLS,
    TX_KEEP_DAYS,
    WORK_END_HOUR,
    WORK_START_HOUR,
)
from .registry import build_registry

log = logging.getLogger(__name__)

DENOMS_ARR = np.array(DENOMS, dtype=np.int64)

# ── Поведенческие профили категорий ──────────────────────────
_PEAKS = {
    "metro":       [(8.0, 1.2, 1.0), (18.5, 1.5, 1.1)],
    "bozor":       [(9.5, 2.0, 1.3)],
    "mall":        [(14.0, 3.0, 1.0), (19.0, 1.5, 0.6)],
    "office":      [(13.0, 1.2, 1.2), (18.0, 1.2, 0.8)],
    "bank_branch": [(11.0, 2.5, 1.0), (15.5, 2.0, 0.7)],
    "residential": [(19.0, 2.0, 1.1), (9.0, 1.5, 0.4)],
    "supermarket": [(18.5, 2.5, 1.1), (12.0, 2.0, 0.5)],
}
_DOW = {
    "metro":       [1.05, 1.00, 1.00, 1.00, 1.10, 0.80, 0.60],
    "bozor":       [0.95, 0.95, 0.95, 0.95, 1.00, 1.25, 1.30],
    "mall":        [0.85, 0.85, 0.85, 0.90, 1.05, 1.40, 1.45],
    "office":      [1.05, 1.00, 1.00, 1.00, 1.10, 0.45, 0.30],
    "bank_branch": [1.10, 1.05, 1.05, 1.05, 1.10, 0.50, 0.25],
    "residential": [0.95, 0.95, 0.95, 1.00, 1.05, 1.10, 1.05],
    "supermarket": [0.90, 0.90, 0.95, 0.95, 1.05, 1.25, 1.20],
}
_HOLIDAY_FACTOR = {
    "metro": 0.70, "bozor": 1.10, "mall": 1.20,
    "office": 0.35, "bank_branch": 0.40, "residential": 0.95, "supermarket": 1.15,
}
_AMOUNT_FACTOR = {
    "metro": 0.90, "bozor": 1.20, "mall": 1.10,
    "office": 1.10, "bank_branch": 1.15, "residential": 1.00, "supermarket": 0.95,
}
_SEASON = {1: 0.90, 2: 0.92, 3: 1.05, 4: 1.00, 5: 1.00, 6: 1.04,
           7: 1.05, 8: 1.05, 9: 1.02, 10: 1.00, 11: 1.00, 12: 1.12}

CARD_BRANDS = [("8600", "Uzcard"), ("9860", "Humo"), ("4", "Visa"), ("5", "Mastercard")]
_BRAND_P = np.array([0.52, 0.38, 0.06, 0.04])


def hour_profile(category: str) -> np.ndarray:
    h = np.arange(24, dtype=float)
    w = np.where((h >= 7) & (h <= 22), 0.55, 0.08)
    for center, width, amp in _PEAKS[category]:
        w = w + amp * np.exp(-0.5 * ((h - center) / width) ** 2)
    return w / w.sum()


def _calendar_days(start: date, end: date) -> Dict[date, dict]:
    days = {}
    d = start
    while d <= end:
        days[d] = {
            "holiday": is_holiday(d),
            "pre": is_pre_holiday(d),
            "post": is_post_holiday(d),
            "salary": is_salary_day(d),
            "after_salary": d.day in (11, 26),
        }
        d += timedelta(days=1)
    return days


def _day_factor(cat: str, d: date, cal: dict) -> float:
    f = _DOW[cat][d.weekday()] * _SEASON[d.month]
    if cal["salary"]:
        f *= 1.9 if cat in ("office", "bank_branch") else 1.6
    elif cal["after_salary"]:
        f *= 1.3
    if cal["holiday"]:
        f *= _HOLIDAY_FACTOR[cat]
    elif cal["pre"]:
        f *= 1.45
    elif cal["post"]:
        f *= 0.9
    return f


# ── Выдача купюр ─────────────────────────────────────────────

def _mix_bills(amounts: np.ndarray) -> np.ndarray:
    """Типовая смешанная выдача: ~60% крупными, остаток мельче."""
    rem = amounts.copy()
    bills = np.zeros((len(amounts), 4), dtype=np.int64)
    n200 = (0.6 * rem) // 200_000
    rem = rem - n200 * 200_000
    n100 = (0.6 * rem) // 100_000
    rem = rem - n100 * 100_000
    n50 = rem // 50_000
    rem = rem - n50 * 50_000
    n10 = rem // 10_000
    bills[:, 3], bills[:, 2], bills[:, 1], bills[:, 0] = n200, n100, n50, n10
    return bills


def _greedy_available(amount: int, avail: np.ndarray) -> np.ndarray | None:
    """Крупными вперёд из того, что есть. None — выдать сумму невозможно."""
    out = np.zeros(4, dtype=np.int64)
    rem = amount
    for j in (3, 2, 1, 0):
        k = min(rem // DENOMS[j], int(avail[j]))
        out[j] = k
        rem -= k * DENOMS[j]
    return out if rem == 0 else None


def _card_masked(token_id: int, rng_brand: int) -> Tuple[str, str]:
    bin_prefix, _ = CARD_BRANDS[rng_brand]
    last4 = f"{(token_id * 7919 + 13) % 10_000:04d}"
    if len(bin_prefix) == 4:
        masked = f"{bin_prefix} •••• •••• {last4}"
    else:
        masked = f"{bin_prefix}••• •••• •••• {last4}"
    token = hashlib.blake2b(f"card:{token_id}".encode(), digest_size=6).hexdigest()
    return masked, f"tok_{token}"


# ── Генерация одного банкомата ───────────────────────────────

def _standard_load(score: float) -> np.ndarray:
    raw = np.array(STANDARD_LOAD_BILLS) * (0.70 + 0.06 * score)
    bills = np.minimum(np.round(raw / BUNDLE_BILLS) * BUNDLE_BILLS, CASSETTE_CAPACITY_BILLS)
    value = float((bills * DENOMS_ARR).sum())
    if value > ATM_MAX_CASH_UZS:
        bills = np.floor(bills * ATM_MAX_CASH_UZS / value / BUNDLE_BILLS) * BUNDLE_BILLS
    return bills.astype(np.int64)


def _next_work_time(t: datetime, rng: np.random.Generator) -> datetime:
    """Сдвигает выезд в рабочее окно инкассации (Пн–Сб, 09–18)."""
    if t.hour < WORK_START_HOUR:
        t = t.replace(hour=WORK_START_HOUR + int(rng.integers(0, 3)))
    elif t.hour >= WORK_END_HOUR:
        t = (t + timedelta(days=1)).replace(hour=WORK_START_HOUR + int(rng.integers(0, 3)))
    if t.weekday() == 6:
        t = t + timedelta(days=1)
    return t


def _schedule_visit(t: datetime, after: datetime, rng: np.random.Generator) -> datetime:
    """Плановый визит: воскресенье переносится на субботу, чтобы банкомат не стоял
    пустым до понедельника (аварийные выезды по-прежнему ждут рабочего дня).
    Перенос только если суббота позже последнего визита `after`."""
    if t.weekday() == 6 and t - timedelta(days=1) > after:
        t = t - timedelta(days=1)
    return _next_work_time(t, rng)


def generate_atm(
    atm: dict,
    idx: int,
    calendar: Dict[date, dict],
    tx_keep_from: datetime,
    offline_from: datetime | None = None,
) -> Tuple[List[tuple], List[tuple], List[tuple]]:
    rng = np.random.default_rng(SEED + 1_000 + idx)
    cat = atm["category"]
    score = atm["geo_score"]
    hp = hour_profile(cat)
    # несколько устройств на одной точке делят спрос, а не дублируют его
    n_dev = max(int(atm.get("devices_at_site") or 1), 1)
    base_daily_tx = (40 + 22 * score) * (1 + 0.6 * (n_dev - 1)) / n_dev

    start = SIM_START
    if atm["installed_at"]:
        start = datetime.fromisoformat(atm["installed_at"])
    maint_from = datetime.fromisoformat(atm["maintenance_from"]) if atm["maintenance_from"] else None

    std_load = _standard_load(score)
    std_value = int((std_load * DENOMS_ARR).sum())
    counts = std_load.copy()
    # Банк планирует визиты так, чтобы стандартной загрузки (≤ 400 млн) хватало с запасом:
    # ~60% загрузки на ожидаемый спрос за цикл, но не реже раза в 4 дня.
    expected_daily = base_daily_tx * 450_000 * _AMOUNT_FACTOR[cat] * np.exp(0.75 ** 2 / 2)
    cycle_days = int(np.clip(0.6 * std_value // expected_daily, 1, 4))
    # маршруты банка разнесены по дням: у каждой точки своя фаза графика
    first_visit_in = int(rng.integers(1, cycle_days + 1))
    next_scheduled = _schedule_visit(
        (start + timedelta(days=first_visit_in)).replace(hour=10 + int(rng.integers(0, 5))), start, rng
    )
    emergency_at: datetime | None = None

    pool_size = int(800 + 250 * score)
    pool_offset = idx * 50_000
    zipf_p = 1.0 / np.arange(1, pool_size + 1) ** 0.8
    zipf_p /= zipf_p.sum()
    brands = rng.choice(len(CARD_BRANDS), size=pool_size, p=_BRAND_P)

    latent = 0.0
    shock = 1.0
    cur_day: date | None = None
    day_f = 1.0
    offline_left = 0

    hourly: List[tuple] = []
    txs: List[tuple] = []
    incs: List[tuple] = []
    tx_seq = 0

    t = start.replace(minute=0, second=0, microsecond=0)
    while t < SIM_NOW:  # часовые корзины [t, t+1h); история заканчивается ровно в SIM_NOW
        d = t.date()
        if d != cur_day:
            cur_day = d
            latent = 0.6 * latent + rng.normal(0, 0.12)
            shock = float(rng.uniform(1.4, 2.2)) if rng.random() < 0.01 else 1.0
            day_f = _day_factor(cat, d, calendar[d]) * np.exp(latent) * shock
            if atm["installed_at"]:
                age = (t - start).total_seconds() / 86_400
                day_f *= 1 - np.exp(-(age + 1) / 7)

        # ── Инкассация ──
        incassation = 0
        loaded_value = 0
        due = [x for x in (next_scheduled, emergency_at) if x is not None and x <= t]
        in_maintenance = maint_from is not None and t >= maint_from
        if due and not in_maintenance:
            kind = "emergency" if emergency_at is not None and emergency_at <= t else "scheduled"
            returned = int((counts * DENOMS_ARR).sum())
            counts = std_load.copy()
            loaded_value = std_value
            incassation = 1
            incs.append((
                atm["atm_id"], t.isoformat(sep=" "), kind,
                *[int(x) for x in counts], loaded_value, returned,
            ))
            emergency_at = None
            next_scheduled = _schedule_visit(
                (t + timedelta(days=cycle_days)).replace(hour=10 + int(rng.integers(0, 5))), t, rng
            )

        # ── Связь / сбои ──
        if offline_left > 0:
            offline_left -= 1
        elif rng.random() < 0.0012:
            offline_left = int(rng.integers(2, 9))
        lost_link = offline_from is not None and t >= offline_from
        online = 0 if (offline_left > 0 or in_maintenance or lost_link) else 1

        n_ok = n_decl = n_nocash = 0
        demand = dispensed = 0
        if online:
            lam = base_daily_tx * day_f * hp[t.hour]
            n = int(rng.poisson(lam))
            if n:
                median = 450_000 * _AMOUNT_FACTOR[cat] * (1.3 if calendar[d]["salary"] else 1.0)
                raw = rng.lognormal(np.log(median), 0.75, size=n)
                step = np.where(rng.random(n) < 0.8, 50_000, 10_000)
                amounts = np.clip(np.round(raw / step) * step, 50_000, 4_000_000).astype(np.int64)
                declined = rng.random(n) < 0.03
                local = rng.random(n) < 0.7
                token_ids = np.where(
                    local,
                    pool_offset + rng.choice(pool_size, size=n, p=zipf_p),
                    3_000_000 + rng.integers(0, 300_000, size=n),
                )
                minutes = np.sort(rng.integers(0, 3_600, size=n))

                bills = _mix_bills(amounts)
                bills[declined] = 0
                cum = np.cumsum(bills, axis=0)
                ok_prefix = np.all(cum <= counts, axis=1)
                if ok_prefix.all():
                    counts = counts - cum[-1]
                    status = np.where(declined, 2, 0)
                else:
                    first_bad = int(np.argmin(ok_prefix))
                    if first_bad > 0:
                        counts = counts - cum[first_bad - 1]
                    status = np.where(declined, 2, 0)
                    for k in range(first_bad, n):
                        if declined[k]:
                            continue
                        out = bills[k] if np.all(bills[k] <= counts) else _greedy_available(int(amounts[k]), counts)
                        if out is None:
                            status[k] = 1
                            bills[k] = 0
                        else:
                            bills[k] = out
                            counts = counts - out

                n_ok = int((status == 0).sum())
                n_nocash = int((status == 1).sum())
                n_decl = int((status == 2).sum())
                demand = int(amounts[status != 2].sum())
                dispensed = int((bills * DENOMS_ARR).sum())

                if t >= tx_keep_from:
                    for k in range(n):
                        tok = int(token_ids[k])
                        brand = int(brands[tok - pool_offset]) if local[k] else tok % 4
                        masked, token = _card_masked(tok, brand)
                        tx_seq += 1
                        ts = t + timedelta(seconds=int(minutes[k]))
                        txs.append((
                            f"{atm['atm_id']}-{tx_seq:07d}", ts.isoformat(sep=" "), atm["atm_id"],
                            masked, token, int(amounts[k]),
                            ("ok", "no_cash", "declined")[int(status[k])],
                            *[int(x) for x in bills[k]],
                        ))

        balance = int((counts * DENOMS_ARR).sum())
        if (
            balance < EMERGENCY_PCT * std_value
            and emergency_at is None
            and not in_maintenance
        ):
            delay = int(rng.integers(*EMERGENCY_DELAY_H))
            emergency_at = _next_work_time(t + timedelta(hours=delay), rng)

        hourly.append((
            atm["atm_id"], t.isoformat(sep=" "), online,
            n_ok + n_nocash + n_decl, n_ok, n_decl, n_nocash,
            demand, dispensed, balance, *[int(x) for x in counts],
            incassation, loaded_value,
        ))
        t += timedelta(hours=1)

    return hourly, txs, incs


# ── Сохранение ───────────────────────────────────────────────

SCHEMA = """
DROP TABLE IF EXISTS atms;
DROP TABLE IF EXISTS hourly;
DROP TABLE IF EXISTS transactions;
DROP TABLE IF EXISTS incassations;
DROP TABLE IF EXISTS connection;

CREATE TABLE atms (
    atm_id TEXT PRIMARY KEY, serial TEXT, bank TEXT, address TEXT, registry_address TEXT,
    address_is_legal INTEGER, district TEXT, payment_systems TEXT, lat REAL, lon REAL,
    category TEXT, category_label TEXT, geo_score REAL, geo_potential REAL, competition_penalty REAL, score_source TEXT,
    geo_breakdown TEXT, nearest_poi TEXT, competitors_500m INTEGER, devices_at_site INTEGER,
    vendor TEXT, model TEXT, variant TEXT, lifecycle TEXT, installed_at TEXT, maintenance_from TEXT
);
CREATE TABLE hourly (
    atm_id TEXT, ts TEXT, online INTEGER, n_tx INTEGER, n_ok INTEGER, n_declined INTEGER,
    n_no_cash INTEGER, demand_amount INTEGER, dispensed_amount INTEGER, balance INTEGER,
    c10 INTEGER, c50 INTEGER, c100 INTEGER, c200 INTEGER, incassation INTEGER, loaded_amount INTEGER,
    PRIMARY KEY (atm_id, ts)
);
CREATE TABLE transactions (
    tx_id TEXT PRIMARY KEY, ts TEXT, atm_id TEXT, card_masked TEXT, card_token TEXT,
    amount INTEGER, status TEXT, b10 INTEGER, b50 INTEGER, b100 INTEGER, b200 INTEGER
);
CREATE INDEX ix_tx_atm_ts ON transactions (atm_id, ts);
CREATE TABLE incassations (
    atm_id TEXT, ts TEXT, kind TEXT, l10 INTEGER, l50 INTEGER, l100 INTEGER, l200 INTEGER,
    loaded_amount INTEGER, returned_amount INTEGER
);
CREATE INDEX ix_inc_atm_ts ON incassations (atm_id, ts);
CREATE TABLE connection (atm_id TEXT PRIMARY KEY, online INTEGER, last_seen TEXT);
"""

# Банкоматы без связи в момент «сейчас»: (индекс в реестре, сколько часов нет связи)
_OFFLINE_NOW = {3: 2, 20: 5, 41: 9}


def generate(db_path=DB_PATH) -> dict:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    atms = build_registry()
    calendar = _calendar_days(SIM_START.date(), SIM_NOW.date() + timedelta(days=10))
    tx_keep_from = SIM_NOW - timedelta(days=TX_KEEP_DAYS)

    conn = sqlite3.connect(db_path)
    conn.executescript(SCHEMA)
    cols = (
        "atm_id", "serial", "bank", "address", "registry_address", "address_is_legal", "district",
        "payment_systems", "lat", "lon", "category", "category_label", "geo_score", "geo_potential",
        "competition_penalty", "score_source",
        "geo_breakdown", "nearest_poi", "competitors_500m", "devices_at_site", "vendor", "model",
        "variant", "lifecycle", "installed_at", "maintenance_from",
    )
    conn.executemany(
        f"INSERT INTO atms VALUES ({','.join('?' * len(cols))})",
        [tuple(json.dumps(a[k], ensure_ascii=False) if isinstance(a[k], (list, dict)) else a[k]
               for k in cols) for a in atms],
    )

    totals = {"hourly": 0, "transactions": 0, "incassations": 0}
    for i, atm in enumerate(atms):
        off_h = _OFFLINE_NOW.get(i)
        offline_from = SIM_NOW - timedelta(hours=off_h - 1) if off_h else None
        hourly, txs, incs = generate_atm(atm, i, calendar, tx_keep_from, offline_from)
        conn.executemany("INSERT INTO hourly VALUES (" + ",".join("?" * 16) + ")", hourly)
        conn.executemany("INSERT INTO transactions VALUES (" + ",".join("?" * 11) + ")", txs)
        conn.executemany("INSERT INTO incassations VALUES (" + ",".join("?" * 9) + ")", incs)

        last_online = next((r[1] for r in reversed(hourly) if r[2] == 1), None)
        is_online = 0 if (off_h or atm["lifecycle"] == "maintenance") else 1
        conn.execute("INSERT INTO connection VALUES (?,?,?)", (atm["atm_id"], is_online, last_online))
        totals["hourly"] += len(hourly)
        totals["transactions"] += len(txs)
        totals["incassations"] += len(incs)
        log.info("  %s (%s, score %.2f): %d ч, %d транзакций",
                 atm["atm_id"], atm["category"], atm["geo_score"], len(hourly), len(txs))

    conn.commit()
    conn.close()
    return {"atms": len(atms), **totals}
