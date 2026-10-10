"""
ML-прогноз аналитики поверх записей реестра платформы.

Связь по идентификатору: terminal_id реестра == atm_id аналитики. Для ATM,
которых нет в аналитике (другой реестр, нет истории), поле `ml` не
заполняется — маршрутизатор тогда работает по порогам остатка.
"""

from __future__ import annotations

from typing import Any, Dict, List

from api.analytics.config import DEFAULT_LEAD_H

# «Критично» — деньги (P50) закончатся раньше, чем экипаж сможет приехать,
# либо в ближайшие сутки; «Предупреждение» — выезд нужен в ближайшее окно,
# но по ожидаемому спросу денег пока хватает (запас уровня сервиса).
CRITICAL_MIN_H = 24


def ml_severity(rec: Dict[str, Any]) -> str:
    if rec["urgency"] not in ("urgent", "today"):
        return "ok"
    h = rec["hours_to_empty"]
    if h is not None and h <= max(CRITICAL_MIN_H, rec["next_window_h"] + DEFAULT_LEAD_H):
        return "critical"
    return "warning"


def attach_ml(atms: List[Dict[str, Any]], service) -> List[Dict[str, Any]]:
    """Добавляет `ml` и пересчитывает `forecast_hours` от текущего остатка платформы."""
    if not service.ready:
        return atms
    for a in atms:
        tid = str(a.get("terminal_id"))
        ml_atm = service.atms.get(tid)
        if ml_atm is None:
            continue
        bal = a.get("balance")
        if bal is not None:
            service.set_balance(tid, int(round(bal)), source=a.get("balance_source") or "platform")
        if ml_atm["lifecycle"] == "maintenance":
            a["ml"] = {"lifecycle": "maintenance", "urgency": None}
            a["forecast_hours"] = None
            continue
        rec = service.recommendation(tid)
        if rec is None:
            continue
        f = service.forecasts.get(tid)
        a["forecast_hours"] = rec["hours_to_empty"]
        a["forecast_source"] = "ml"
        severity = ml_severity(rec)
        # статус на карте = решение ML; остаток < 20% — всегда «критично» (страховка)
        a.setdefault("balance_status", a.get("status"))
        a["status"] = "critical" if a["balance_status"] == "critical" else severity
        a["ml"] = {
            "lifecycle": ml_atm["lifecycle"],
            "severity": severity,
            "next_window_h": rec["next_window_h"],
            "next_window_at": rec["next_window_at"],
            "first_cassette_empty": rec["first_cassette_empty"],
            "urgency": rec["urgency"],
            "visit_by": rec["visit_by"],
            "hours_to_empty": rec["hours_to_empty"],
            "empty_at": rec["empty_at"],
            "load_value": rec["load"]["value"],
            "balance_at_visit": rec["balance_at_visit"],
            "topup_value": rec["topup_value"],
            "capacity_limited": rec["capacity_limited"],
            "covers_h": rec["covers_h"],
            "service_level": rec["service_level"],
            "cashout_prob_24h": service.forecaster.cashout_prob_24h(f, ml_atm["balance"]) if f else None,
        }
    return atms
