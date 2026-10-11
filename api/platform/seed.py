"""
Синтетическое наполнение платформы (вместо реальных выгрузок банка).

Чтобы все страницы работали из коробки, при пустой базе:

  * bank.db / atms, branches — реестр из синтетического реестра аналитики
    (api/analytics/registry.py) и вымышленные пункты инкассации;
  * atm_monitor.db — телеметрия: почасовой остаток, статус связи, кассеты,
    прогноз часов до опустошения (из модели XGBoost, api/analytics).

Отключить: SEED_SYNTHETIC=0.
"""

from __future__ import annotations

import logging
import os
import sqlite3
from datetime import datetime, timedelta, timezone
from typing import List

from atm_monitor.config import settings as monitor_settings
from atm_monitor.db import connect as monitor_connect, init_db as monitor_init_db
from api.analytics.config import DB_PATH as ANALYTICS_DB, DENOMS, SIM_NOW
from .config import MAX_CAPACITY
from .db import bulk_insert_atms, bulk_insert_branches, count_atms

log = logging.getLogger(__name__)

REGION = "Тошкент шаҳри"
TASHKENT_UTC_OFFSET = timedelta(hours=5)
HISTORY_DAYS = 30

# Вымышленные пункты инкассации по районам Ташкента (координаты — центры районов)
SYNTHETIC_BRANCHES = [
    {"local_code": "90001", "number": "Пункт инкассации «Юнусабад»", "lat": 41.3640, "lon": 69.2860},
    {"local_code": "90002", "number": "Пункт инкассации «Мирабад»", "lat": 41.2990, "lon": 69.2790},
    {"local_code": "90003", "number": "Пункт инкассации «Чиланзар»", "lat": 41.2850, "lon": 69.2040},
    {"local_code": "90004", "number": "Пункт инкассации «Алмазар»", "lat": 41.3440, "lon": 69.2150},
    {"local_code": "90005", "number": "Пункт инкассации «Мирзо-Улугбек»", "lat": 41.3290, "lon": 69.3330},
    {"local_code": "90006", "number": "Пункт инкассации «Сергели»", "lat": 41.2270, "lon": 69.2200},
]
for _b in SYNTHETIC_BRANCHES:
    _b["address"] = f"{_b['number']} (синтетика)"


def _utc(local_iso: str) -> str:
    t = datetime.fromisoformat(local_iso) - TASHKENT_UTC_OFFSET
    return t.replace(tzinfo=timezone.utc).isoformat()


def _nearest_branch(lat: float, lon: float) -> dict:
    return min(SYNTHETIC_BRANCHES, key=lambda b: (b["lat"] - lat) ** 2 + (b["lon"] - lon) ** 2)


def _seed_registry(service) -> int:
    records = []
    for a in service.atms.values():
        br = _nearest_branch(a["lat"], a["lon"])
        records.append({
            "terminal_id": a["atm_id"],
            "atm_number": a["atm_id"][-4:],
            "branch_code": br["local_code"],
            "local_code": br["local_code"],
            "region": REGION,
            "branch": br["number"],
            "model": f"{a['vendor']} {a['model']}",
            "network_type": a.get("payment_systems") or "Uzcard / Humo",
            "serial": a["serial"],
            "merchant_id": f"M{a['atm_id']}",
            "address": a["address"],
            "lat": a["lat"],
            "lon": a["lon"],
            "capacity": MAX_CAPACITY,
        })
    stats = bulk_insert_atms(records)
    bulk_insert_branches([{**b, "region": REGION, "incassation": 1} for b in SYNTHETIC_BRANCHES])
    return stats["inserted"]


def _seed_monitoring(service) -> int:
    monitor_init_db()
    with monitor_connect(readonly=True) as conn:
        if conn.execute("SELECT COUNT(*) FROM atm_snapshots").fetchone()[0]:
            return 0

    since = (SIM_NOW - timedelta(days=HISTORY_DAYS)).isoformat(sep=" ")
    src = sqlite3.connect(f"file:{ANALYTICS_DB}?mode=ro", uri=True)
    hours: List[str] = [r[0] for r in src.execute(
        "SELECT DISTINCT ts FROM hourly WHERE ts >= ? ORDER BY ts", (since,)
    )]

    with monitor_connect() as conn:
        run_ids = {}
        for ts in hours:
            polled = _utc((datetime.fromisoformat(ts) + timedelta(hours=1)).isoformat(sep=" "))
            cur = conn.execute(
                "INSERT INTO poll_runs (started_at, finished_at, status, atm_count, error_message) "
                "VALUES (?,?,?,?,?)",
                (polled, polled, "success", len(service.atms), "synthetic"),
            )
            run_ids[ts] = (cur.lastrowid, polled)

        for i, a in enumerate(service.atms.values(), start=1):
            conn.execute(
                """INSERT INTO atms (id, serial, tid, model_name, vendor_name, variant_name, type_name,
                       terminal_id, address, place, latitude, longitude, timezone_offset, status, last_seen_at)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (i, a["serial"], a["atm_id"], a["model"], a["vendor"], a["variant"], "ATM",
                 a["atm_id"], a["address"], a["category_label"], a["lat"], a["lon"], 300,
                 "production", _utc(SIM_NOW.isoformat(sep=" "))),
            )
            last_inc = None
            rows = src.execute(
                "SELECT ts, online, balance, incassation FROM hourly WHERE atm_id=? AND ts>=? ORDER BY ts",
                (a["atm_id"], since),
            ).fetchall()
            for ts, online, balance, inc in rows:
                run_id, polled = run_ids[ts]
                if inc:
                    last_inc = _utc(ts)
                conn.execute(
                    """INSERT INTO atm_snapshots (atm_id, poll_run_id, polled_at, agent_status,
                           agent_last_online, cdm_total_uzs, last_transaction_cash_in, dispenser_status)
                       VALUES (?,?,?,?,?,?,?,?)""",
                    (i, run_id, polled, "ONLINE" if online else "OFFLINE",
                     polled if online else None, float(balance), last_inc, "OK" if online else "UNKNOWN"),
                )
            snap_id = conn.execute("SELECT MAX(id) FROM atm_snapshots WHERE atm_id=?", (i,)).fetchone()[0]
            polled = run_ids[rows[-1][0]][1] if rows else _utc(SIM_NOW.isoformat(sep=" "))
            for c in a["cassettes"]:
                status = "EMPTY" if c["count"] == 0 else "LOW" if c["fill_pct"] < 15 else "OK"
                conn.execute(
                    """INSERT INTO cassette_snapshots (atm_snapshot_id, atm_id, polled_at, cassette_index,
                           unit_id, cassette_type, status, count, currency, nominal)
                       VALUES (?,?,?,?,?,?,?,?,?,?)""",
                    (snap_id, i, polled, c["slot"], f"CDM{c['slot']}", "BILLCASSETTE", status,
                     c["count"], "UZS", c["denom"]),
                )
            rec = service.recommendation(a["atm_id"])
            conn.execute(
                """INSERT INTO turnover_snapshots (atm_id, polled_at, device, currency, balance, forecast_hours)
                   VALUES (?,?,?,?,?,?)""",
                (i, polled, "CDM", "UZS", float(a["balance"]),
                 float(rec["hours_to_empty"]) if rec and rec["hours_to_empty"] is not None else None),
            )
    src.close()
    return len(service.atms)


def seed_if_empty(service) -> None:
    if os.getenv("SEED_SYNTHETIC", "1") == "0" or not service.ready:
        return
    if count_atms() == 0:
        n = _seed_registry(service)
        log.info("Синтетический реестр: %d ATM, %d пунктов инкассации", n, len(SYNTHETIC_BRANCHES))
    n = _seed_monitoring(service)
    if n:
        log.info("Синтетический мониторинг: %d ATM × %d дней (%s)", n, HISTORY_DAYS, monitor_settings.DB_PATH)
