"""
Единый модуль календаря для Узбекистана.
Импортируется везде: генератор, симулятор, предиктор, train_model.

Зарплатные дни: 10-е и 25-е число каждого месяца
(стандарт большинства гос. и частных предприятий Узбекистана).
"""

from __future__ import annotations
from datetime import date, datetime, timedelta
from typing import Optional, Union

# ── Зарплатные дни ───────────────────────────────────────────
SALARY_DAYS: frozenset[int] = frozenset({10, 25})
NEAR_SALARY_DELTA: int = 2        # ±2 дня считается «около зарплаты»

# ── Фиксированные государственные праздники (месяц, день) ────
# Источник: ТК РУз, lex.uz, производственные календари
UZ_HOLIDAY_NAMES: dict[tuple[int, int], str] = {
    (1,  1):  "Yangi yil (Новый год)",
    (1,  2):  "Yangi yil dam olishi (Новый год)",
    (1,  14): "Vatan himoyachilari kuni (День защитников Родины)",
    (3,  8):  "Xotin-qizlar kuni (8 Марта)",
    (3,  21): "Navro'z bayrami",
    (3,  22): "Navro'z bayrami",
    (3,  23): "Navro'z bayrami",
    (3,  24): "Navro'z bayrami",
    (5,  9):  "Xotira va qadrlash kuni (День памяти и почёта)",
    (9,  1):  "Mustaqillik kuni (День независимости)",
    (10, 1):  "O'qituvchilar kuni (День учителя)",
    (12, 8):  "Konstitutsiya kuni (День Конституции)",
}

UZ_HOLIDAYS: frozenset[tuple[int, int]] = frozenset(UZ_HOLIDAY_NAMES.keys())

# ── Исламские праздники по годам (даты уточняются ежегодно) ──
ISLAMIC_HOLIDAY_NAMES: dict[int, dict[tuple[int, int], str]] = {
    2023: {
        (4, 21): "Ramazon Hayit (Ид аль-Фитр)",
        (4, 22): "Ramazon Hayit (Ид аль-Фитр)",
        (6, 28): "Qurbon Hayit (Ид аль-Адха)",
        (6, 29): "Qurbon Hayit (Ид аль-Адха)",
    },
    2024: {
        (4, 10): "Ramazon Hayit (Ид аль-Фитр)",
        (4, 11): "Ramazon Hayit (Ид аль-Фитр)",
        (6, 16): "Qurbon Hayit (Ид аль-Адха)",
        (6, 17): "Qurbon Hayit (Ид аль-Адха)",
    },
    2025: {
        (3, 30): "Ramazon Hayit (Ид аль-Фитр)",
        (3, 31): "Ramazon Hayit (Ид аль-Фитр)",
        (6,  6): "Qurbon Hayit (Ид аль-Адха)",
        (6,  7): "Qurbon Hayit (Ид аль-Адха)",
    },
    2026: {
        (3, 20): "Ramazon Hayit (Ид аль-Фитр)",
        (3, 21): "Ramazon Hayit (Ид аль-Фитр)",
        (5, 27): "Qurbon Hayit (Ид аль-Адха)",
        (5, 28): "Qurbon Hayit (Ид аль-Адха)",
        (5, 29): "Qurbon Hayit (Ид аль-Адха)",
    },
}

# Дополнительные нерабочие / перенесённые дни (президентские постановления)
UZ_EXTRA_HOLIDAY_NAMES: dict[int, dict[tuple[int, int], str]] = {
    2024: {
        (3, 22): "Navro'z (доп. выходной)",
        (4, 11): "Ramazon Hayit (доп. выходной)",
        (6, 18): "Qurbon Hayit (доп. выходной)",
        (9,  2): "Mustaqillik kuni (перенос с 01.09)",
        (12, 9): "Konstitutsiya kuni (перенос с 08.12)",
        (12, 30): "Доп. выходной (Новый год)",
        (12, 31): "Доп. выходной (Новый год)",
    },
    2025: {
        (12, 31): "Доп. выходной (Новый год)",
    },
    2026: {
        (1,  2): "Yangi yil (доп. выходной)",
        (3,  9): "8 Mart (перенос)",
        (3, 23): "Navro'z (перенос)",
        (5, 11): "9 May (перенос)",
        (5, 28): "Qurbon Hayit (доп. выходной)",
        (5, 29): "Qurbon Hayit (доп. выходной)",
        (5, 30): "Qurbon Hayit (доп. выходной)",
        (8, 31): "Доп. выходной (перенос)",
        (12, 31): "Доп. выходной (перенос)",
    },
}


def _as_datetime(dt: Union[date, datetime]) -> datetime:
    if isinstance(dt, datetime):
        return dt
    return datetime(dt.year, dt.month, dt.day)


def is_salary_day(day: Union[int, date, datetime]) -> bool:
    d = day if isinstance(day, int) else (day.day if hasattr(day, "day") else int(day))
    return d in SALARY_DAYS


def is_near_salary(day: Union[int, date, datetime]) -> bool:
    d = day if isinstance(day, int) else (day.day if hasattr(day, "day") else int(day))
    return any(abs(d - s) <= NEAR_SALARY_DELTA for s in SALARY_DAYS)


def _next_day(dt: Union[date, datetime]) -> datetime:
    return _as_datetime(dt) + timedelta(days=1)


def _prev_day(dt: Union[date, datetime]) -> datetime:
    return _as_datetime(dt) - timedelta(days=1)


def get_holiday_name(dt: Union[date, datetime]) -> Optional[str]:
    """Название праздника, если день является нерабочим праздником Узбекистана."""
    base = _as_datetime(dt)
    md = (base.month, base.day)
    year = base.year

    if md in UZ_HOLIDAY_NAMES:
        return UZ_HOLIDAY_NAMES[md]
    if md in ISLAMIC_HOLIDAY_NAMES.get(year, {}):
        return ISLAMIC_HOLIDAY_NAMES[year][md]
    if md in UZ_EXTRA_HOLIDAY_NAMES.get(year, {}):
        return UZ_EXTRA_HOLIDAY_NAMES[year][md]
    return None


def is_holiday(dt: Union[date, datetime]) -> bool:
    return get_holiday_name(dt) is not None


def is_pre_holiday(dt: Union[date, datetime]) -> bool:
    """День перед государственным / религиозным праздником."""
    return is_holiday(_next_day(dt))


def is_post_holiday(dt: Union[date, datetime]) -> bool:
    """День после праздника."""
    return is_holiday(_prev_day(dt))


def peak_demand_reason(dt: Union[date, datetime]) -> Optional[str]:
    """
    Причина повышенного спроса на наличные.
    Объединяет зарплатные и праздничные дни для UI/мониторинга.
    ML-признаки остаются раздельными (is_salary_day, is_holiday, ...).
    """
    if is_salary_day(dt):
        return "salary"
    if is_holiday(dt):
        return "holiday"
    if is_pre_holiday(dt):
        return "pre_holiday"
    if is_post_holiday(dt):
        return "post_holiday"
    if is_near_salary(dt):
        return "near_salary"
    return None


def is_peak_demand_day(dt: Union[date, datetime]) -> bool:
    return peak_demand_reason(dt) is not None


PEAK_DEMAND_LABELS: dict[str, str] = {
    "salary":       "Зарплатный день",
    "near_salary":  "Скоро зарплата (±2 дня)",
    "holiday":      "Праздник",
    "pre_holiday":  "Предпраздничный день",
    "post_holiday": "После праздника",
}


def peak_demand_label(dt: Union[date, datetime]) -> Optional[str]:
    reason = peak_demand_reason(dt)
    if not reason:
        return None
    if reason == "holiday":
        name = get_holiday_name(dt)
        return name or PEAK_DEMAND_LABELS["holiday"]
    if reason == "pre_holiday":
        name = get_holiday_name(_next_day(dt))
        return f"Предпраздничный: {name}" if name else PEAK_DEMAND_LABELS["pre_holiday"]
    if reason == "post_holiday":
        name = get_holiday_name(_prev_day(dt))
        return f"После праздника: {name}" if name else PEAK_DEMAND_LABELS["post_holiday"]
    return PEAK_DEMAND_LABELS.get(reason)


def season_factor(month: int) -> float:
    """Сезонный коэффициент спроса на наличные."""
    if month in (12, 1, 2):
        return 0.8    # зима — меньше снятий
    if month in (6, 7, 8):
        return 1.2    # лето — больше
    return 1.0


def build_holiday_map(date_from: date, date_to: date) -> dict[date, str]:
    """Все праздники в диапазоне дат — для генератора датасета."""
    result: dict[date, str] = {}
    cur = date_from
    while cur <= date_to:
        name = get_holiday_name(cur)
        if name:
            result[cur] = name
        cur += timedelta(days=1)
    return result
