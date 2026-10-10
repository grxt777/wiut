"""
Реальные данные банкоматов от сборщика atm_monitor (пакет `atm_monitor/` в
этом же проекте: опрашивает monitoring.btech.uz раз в час и пишет в SQLite
`data/atm_monitor.db`; стартует вместе с сервером, см. atm_monitor/scheduler.py).

Этот модуль — read-only клиент той базы. Если данных ещё нет (сборщик не
настроен или первый опрос не завершён) — функции возвращают пустой результат,
дашборд продолжает работать на данных из реестра, просто без «живого» баланса.
"""
from __future__ import annotations

import logging
import math
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from atm_monitor.db import connect

log = logging.getLogger(__name__)

# Реальный номинал купюры в Узбекистане: 1000 и выше. Платформа иногда отдаёт
# "кассеты" с nominal 5/6/7/50/100 и т.п. — это служебные/пустые слоты, а не
# заряженные купюры, их не показываем как деньги.
MIN_REAL_NOMINAL = 1000

_CACHE_TTL_SEC = 60
_states_cache: Dict[str, Any] = {"at": 0.0, "data": {}}
_cassettes_cache: Dict[str, Any] = {"at": 0.0, "data": {}}

# id последнего снимка банкомата `a` (коррелированный подзапрос вместо LATERAL)
_LAST_SNAPSHOT_ID = (
    "SELECT s.id FROM atm_snapshots s WHERE s.atm_id = a.id "
    "ORDER BY s.polled_at DESC, s.id DESC LIMIT 1"
)


def _query(sql: str, params: tuple = ()) -> List[Dict[str, Any]]:
    try:
        with connect(readonly=True) as conn:
            return [dict(r) for r in conn.execute(sql, params).fetchall()]
    except Exception as e:  # noqa: BLE001 — база сборщика может быть ещё пустой
        log.warning("База atm_monitor недоступна: %s", e)
        return []


def latest_states_by_tid(force: bool = False) -> Dict[str, Dict[str, Any]]:
    """tid -> {balance, agent_status, agent_last_online, forecast_hours, polled_at}.

    Кэшируется на _CACHE_TTL_SEC, чтобы не бить базу на каждый list_atms().
    """
    now = time.time()
    if not force and (now - _states_cache["at"]) < _CACHE_TTL_SEC:
        return _states_cache["data"]

    rows = _query(
        f"""
        SELECT
            a.tid AS tid,
            a.latitude AS latitude,
            a.longitude AS longitude,
            snap.cdm_total_uzs AS balance,
            snap.agent_status AS agent_status,
            snap.agent_last_online AS agent_last_online,
            snap.polled_at AS polled_at,
            snap.last_transaction_cash_in AS last_incassation,
            (SELECT t.forecast_hours FROM turnover_snapshots t
              WHERE t.atm_id = a.id ORDER BY t.polled_at DESC, t.id DESC LIMIT 1) AS forecast_hours
        FROM atms a
        JOIN atm_snapshots snap ON snap.id = ({_LAST_SNAPSHOT_ID})
        WHERE a.tid IS NOT NULL
        """
    )

    data = {}
    for r in rows:
        tid = str(r["tid"])
        data[tid] = {
            "lat": float(r["latitude"]) if r.get("latitude") is not None else None,
            "lon": float(r["longitude"]) if r.get("longitude") is not None else None,
            "balance": float(r["balance"]) if r.get("balance") is not None else None,
            "agent_status": r.get("agent_status"),
            "agent_last_online": r.get("agent_last_online"),
            "forecast_hours": float(r["forecast_hours"]) if r.get("forecast_hours") is not None else None,
            "polled_at": r.get("polled_at"),
            "last_incassation": r.get("last_incassation"),
        }

    _states_cache["at"] = now
    _states_cache["data"] = data
    return data


def _haversine_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    R = 6371000.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dlambda / 2) ** 2
    return 2 * R * math.asin(math.sqrt(a))


def find_nearest_tid(lat: Optional[float], lon: Optional[float], max_distance_m: float = 100) -> Optional[str]:
    """Резерв на случай, когда terminal_id в реестре не совпадает с tid BTech
    (разные системы нумерации у разных сетей — WAY4/UzCard и т.п.), но это
    физически тот же банкомат: реестр и BTech почти всегда указывают на один
    и тот же адрес с точностью в единицы-десятки метров.

    Матчит только координаты — при плотной застройке (несколько ATM в одном
    здании/на одной улице) может по ошибке зацепить банкомат-сосед, а не
    буквально тот же физический аппарат; это компромисс, а не гарантия."""
    if lat is None or lon is None:
        return None
    states = latest_states_by_tid()
    deg_margin = max_distance_m / 111_000  # грубая оценка градуса в метрах, с запасом
    best_tid, best_dist = None, max_distance_m
    for tid, s in states.items():
        slat, slon = s.get("lat"), s.get("lon")
        if slat is None or slon is None:
            continue
        if abs(slat - lat) > deg_margin or abs(slon - lon) > deg_margin:
            continue
        d = _haversine_m(lat, lon, slat, slon)
        if d < best_dist:
            best_dist, best_tid = d, tid
    return best_tid


def _row_to_cassette(r: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "index": r.get("cassette_index"),
        "type": r.get("cassette_type"),
        "status": r.get("status"),
        "count": r.get("count"),
        "currency": r.get("currency"),
        "nominal": r.get("nominal"),
        "balance": (r["count"] * r["nominal"]) if r.get("count") is not None and r.get("nominal") is not None else None,
    }


# Только "живые" кассеты купюр: REJECTCASSETTE/RETRACTCASSETTE/RECYCLING — это
# отбракованные/принятые при депозите купюры, а не выдаваемый остаток, и мы их
# не показываем как деньги, которые можно снять.
_CASSETTE_FILTER_SQL = "c.cassette_type = 'BILLCASSETTE' AND c.nominal >= ?"


def cassettes_by_tid(tid: str) -> List[Dict[str, Any]]:
    """Реальные кассеты банкомата на момент последнего опроса (пусто, если ATM не в BTech).

    Список, а не словарь по номиналу: у части моделей встречается несколько
    кассет с одним и тем же номиналом (например, две по 200 000 — одна LOW,
    другая INOP) — группировка по номиналу потеряла бы одну из них."""
    rows = _query(
        f"""
        SELECT c.cassette_index, c.cassette_type, c.status, c.count, c.currency, c.nominal
        FROM atms a
        JOIN cassette_snapshots c ON c.atm_snapshot_id = ({_LAST_SNAPSHOT_ID})
        WHERE a.tid = ? AND {_CASSETTE_FILTER_SQL}
        ORDER BY c.cassette_index
        """,
        (str(tid), MIN_REAL_NOMINAL),
    )
    return [_row_to_cassette(r) for r in rows]


def all_latest_cassettes(force: bool = False) -> Dict[str, List[Dict[str, Any]]]:
    """tid -> [кассеты] для ВСЕХ банкоматов одним запросом (для списка ATM без
    N+1 запроса на каждый). Кэшируется так же, как latest_states_by_tid."""
    now = time.time()
    if not force and (now - _cassettes_cache["at"]) < _CACHE_TTL_SEC:
        return _cassettes_cache["data"]

    rows = _query(
        f"""
        SELECT a.tid AS tid, c.cassette_index, c.cassette_type, c.status, c.count, c.currency, c.nominal
        FROM atms a
        JOIN cassette_snapshots c ON c.atm_snapshot_id = ({_LAST_SNAPSHOT_ID})
        WHERE a.tid IS NOT NULL AND {_CASSETTE_FILTER_SQL}
        ORDER BY a.tid, c.cassette_index
        """,
        (MIN_REAL_NOMINAL,),
    )

    data: Dict[str, List[Dict[str, Any]]] = {}
    for r in rows:
        data.setdefault(str(r["tid"]), []).append(_row_to_cassette(r))

    _cassettes_cache["at"] = now
    _cassettes_cache["data"] = data
    return data


def balance_history_by_tid(tid: str, days: int = 30) -> List[Dict[str, Any]]:
    """Реальный ряд общего остатка UZS по времени — задел под прогноз."""
    since = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    rows = _query(
        """
        SELECT s.polled_at, s.cdm_total_uzs
        FROM atms a
        JOIN atm_snapshots s ON s.atm_id = a.id
        WHERE a.tid = ? AND s.polled_at >= ?
        ORDER BY s.polled_at
        """,
        (str(tid), since),
    )
    return [
        {
            "polled_at": r.get("polled_at"),
            "balance": float(r["cdm_total_uzs"]) if r.get("cdm_total_uzs") is not None else None,
        }
        for r in rows
    ]


def collector_status() -> Dict[str, Any]:
    """Статус самого сборщика atm_monitor (последний прогон опроса BTech),
    а не отдельного банкомата — для виджета на главной странице дашборда."""
    rows = _query(
        """
        SELECT id, started_at, finished_at, status, atm_count, error_message
        FROM poll_runs
        ORDER BY id DESC
        LIMIT 1
        """
    )
    if not rows:
        return {
            "connected": False,
            "last_poll_id": None,
            "started_at": None,
            "finished_at": None,
            "status": None,
            "atm_count": None,
            "error": None,
        }
    r = rows[0]
    return {
        "connected": True,
        "last_poll_id": r.get("id"),
        "started_at": r.get("started_at"),
        "finished_at": r.get("finished_at"),
        "status": r.get("status"),
        "atm_count": r.get("atm_count"),
        "error": r.get("error_message"),
    }
