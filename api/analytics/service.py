"""
Сервисный слой аналитики: состояние банкоматов, прогноз, рекомендации,
транзакции. Прогнозы считаются один раз при старте и кешируются;
рекомендация пересчитывается под параметры пользователя (цикл, ставка).
"""

from __future__ import annotations

import json
import logging
import sqlite3
import threading
from typing import Dict, List, Optional

import numpy as np
import pandas as pd

from .config import (
    ATM_MAX_CASH_UZS,
    CASSETTE_CAPACITY_BILLS,
    COST_OF_CAPITAL_PCT,
    DB_PATH,
    DEFAULT_CYCLE_H,
    DENOMS,
    METRICS_PATH,
    MODELS_DIR,
    SIM_NOW,
)
from .forecast import Forecaster, recommend
from .generator import _standard_load
from .registry import LIFECYCLE_LABELS

log = logging.getLogger(__name__)

DENOMS_ARR = np.array(DENOMS)


class AnalyticsService:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.ready = False
        self.atms: Dict[str, dict] = {}
        self.forecasts: Dict[str, dict] = {}
        self.metrics: dict = {}

    # ── Загрузка ──
    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        return conn

    def ensure_built(self) -> None:
        if DB_PATH.exists() and (MODELS_DIR / "demand_xgb.json").exists():
            return
        log.warning("Данные аналитики не найдены — запускаю сборку (≈1–2 мин)")
        from .build import main as build_main
        build_main([])

    def load(self) -> None:
        with self._lock:
            if self.ready:
                return
            self.ensure_built()
            conn = self._connect()
            self.metrics = json.loads(METRICS_PATH.read_text(encoding="utf-8")) if METRICS_PATH.exists() else {}
            fc = Forecaster(conn)
            self.forecaster = fc
            now_s = SIM_NOW.isoformat(sep=" ")
            share_from = (pd.Timestamp(SIM_NOW) - pd.Timedelta(days=14)).isoformat(sep=" ")

            global_mix = conn.execute(
                "SELECT SUM(b10)*10000, SUM(b50)*50000, SUM(b100)*100000, SUM(b200)*200000 "
                "FROM transactions WHERE status='ok'"
            ).fetchone()
            global_shares = np.array(global_mix, dtype=float) / max(sum(global_mix), 1)

            for row in conn.execute("SELECT * FROM atms"):
                a = dict(row)
                for k in ("geo_breakdown", "nearest_poi"):
                    a[k] = json.loads(a[k]) if a.get(k) else ([] if k == "geo_breakdown" else {})
                a["address_is_legal"] = bool(a.get("address_is_legal"))
                atm_id = a["atm_id"]
                last = conn.execute(
                    "SELECT * FROM hourly WHERE atm_id=? AND ts<? ORDER BY ts DESC LIMIT 1",
                    (atm_id, now_s),
                ).fetchone()
                link = conn.execute("SELECT * FROM connection WHERE atm_id=?", (atm_id,)).fetchone()
                inc = conn.execute(
                    "SELECT ts, kind, loaded_amount FROM incassations WHERE atm_id=? ORDER BY ts DESC LIMIT 1",
                    (atm_id,),
                ).fetchone()
                mix = conn.execute(
                    "SELECT SUM(b10)*10000, SUM(b50)*50000, SUM(b100)*100000, SUM(b200)*200000 "
                    "FROM transactions WHERE atm_id=? AND status='ok' AND ts>=?",
                    (atm_id, share_from),
                ).fetchone()
                stats_7d = conn.execute(
                    "SELECT SUM(n_tx), SUM(n_no_cash), SUM(dispensed_amount) FROM hourly "
                    "WHERE atm_id=? AND ts>?",
                    (atm_id, (pd.Timestamp(SIM_NOW) - pd.Timedelta(days=7)).isoformat(sep=" ")),
                ).fetchone()

                mix_v = np.array([x or 0 for x in mix], dtype=float)
                shares = mix_v / mix_v.sum() if mix_v.sum() > 0 else global_shares
                counts = [last["c10"], last["c50"], last["c100"], last["c200"]]
                std_load = _standard_load(a["geo_score"])

                a.update({
                    "lifecycle_label": LIFECYCLE_LABELS[a["lifecycle"]],
                    "online": bool(link["online"]),
                    "last_seen": link["last_seen"],
                    "balance": int(last["balance"]),
                    "cassettes": [
                        {
                            "slot": j + 1, "denom": d, "count": int(counts[j]),
                            "value": int(counts[j] * d),
                            "fill_pct": round(counts[j] / CASSETTE_CAPACITY_BILLS * 100, 1),
                        }
                        for j, d in enumerate(DENOMS)
                    ],
                    "denom_shares": [round(float(x), 4) for x in shares],
                    "standard_load_bills": [int(x) for x in std_load],
                    "standard_load_value": int((std_load * DENOMS_ARR).sum()),
                    "last_incassation": dict(inc) if inc else None,
                    "tx_7d": int(stats_7d[0] or 0),
                    "no_cash_7d": int(stats_7d[1] or 0),
                    "dispensed_7d": int(stats_7d[2] or 0),
                })
                a["balance_source"] = "analytics"
                self.atms[atm_id] = a
                # банкомат на обслуживании не выдаёт наличные — прогноз и выезд не планируются
                if a["lifecycle"] == "maintenance":
                    continue
                f = fc.forecast(atm_id)
                if f is not None:
                    self.forecasts[atm_id] = f
            conn.close()
            self.ready = True
            log.info("Аналитика: %d ATM, прогнозы готовы", len(self.atms))

    # ── Рекомендация ──
    def recommendation(self, atm_id: str, cycle_h: int = DEFAULT_CYCLE_H,
                       rate_pct: float = COST_OF_CAPITAL_PCT) -> Optional[dict]:
        a = self.atms.get(atm_id)
        f = self.forecasts.get(atm_id)
        if not a or f is None or a["lifecycle"] == "maintenance":
            return None
        fc = self.forecaster
        return recommend(
            f, a["balance"], [c["count"] for c in a["cassettes"]], a["denom_shares"],
            fc.lo, fc.hi, pd.Timestamp(SIM_NOW),
            a["standard_load_value"], cycle_h=cycle_h, rate_pct=rate_pct,
            safe=fc.safe, service_level=fc.service_level,
        )

    def set_balance(self, atm_id: str, balance: int, source: str = "manual") -> bool:
        """Остаток из платформы (ввод оператора или свежий опрос) свежее снимка аналитики.

        Кассеты масштабируются пропорционально (или раскладываются по
        структуре выдачи, если банкомат был пуст), чтобы прогноз первой
        опустевшей кассеты и загрузка считались от того же остатка."""
        with self._lock:
            a = self.atms.get(atm_id)
            if a is None:
                return False
            balance = min(max(int(balance), 0), ATM_MAX_CASH_UZS)
            if balance == a["balance"]:
                if source == "manual":
                    a["balance_source"] = source
                return True
            old = sum(c["value"] for c in a["cassettes"])
            for j, c in enumerate(a["cassettes"]):
                if old > 0:
                    value = c["value"] * balance / old
                else:
                    value = balance * a["denom_shares"][j]
                count = int(min(round(value / c["denom"]), CASSETTE_CAPACITY_BILLS))
                c.update({
                    "count": count, "value": count * c["denom"],
                    "fill_pct": round(count / CASSETTE_CAPACITY_BILLS * 100, 1),
                })
            a["balance"] = balance
            a["balance_source"] = source
            return True

    def _row(self, a: dict, rec: Optional[dict]) -> dict:
        return {
            k: a[k] for k in (
                "atm_id", "serial", "bank", "address", "district", "lat", "lon", "category", "category_label",
                "geo_score", "score_source", "vendor", "model", "variant", "lifecycle",
                "lifecycle_label", "online", "last_seen", "balance", "balance_source", "cassettes",
                "no_cash_7d",
            )
        } | {
            "forecast": None if rec is None else {
                "empty_at": rec["empty_at"],
                "hours_to_empty": rec["hours_to_empty"],
                "empty_window": rec["empty_window"],
                "urgency": rec["urgency"],
                "visit_by": rec["visit_by"],
                "load_value": rec["load"]["value"],
                "topup_value": rec["topup_value"],
                "balance_at_visit": rec["balance_at_visit"],
                "capacity_limited": rec["capacity_limited"],
                "first_cassette_empty": rec["first_cassette_empty"],
            },
        }

    # ── Публичные методы ──
    def list_atms(self, tab: str = "active", conn: str = "all", q: str = "",
                  sort: str = "hours_to_empty", order: str = "asc") -> dict:
        rows = []
        q = (q or "").strip().lower()
        counts = {"active": 0, "pilot": 0, "maintenance": 0, "all": 0}
        for a in self.atms.values():
            counts[a["lifecycle"]] += 1
            counts["all"] += 1
            if tab != "all" and a["lifecycle"] != tab:
                continue
            if conn == "online" and not a["online"]:
                continue
            if conn == "offline" and a["online"]:
                continue
            if q and not any(q in str(a[k]).lower() for k in ("atm_id", "serial", "address", "category_label", "model")):
                continue
            rows.append(self._row(a, self.recommendation(a["atm_id"])))

        def key(r):
            fc = r["forecast"] or {}
            if sort == "hours_to_empty":
                v = fc.get("hours_to_empty")
                return v if v is not None else 10_000
            if sort == "balance":
                return r["balance"]
            if sort == "geo_score":
                return r["geo_score"]
            if sort == "load_value":
                return fc.get("load_value") or 0
            return str(r.get(sort, ""))

        rows.sort(key=key, reverse=(order == "desc"))
        return {"items": rows, "count": len(rows), "tabs": counts, "now": SIM_NOW.isoformat(sep=" ")}

    def summary(self) -> dict:
        recs = {i: self.recommendation(i) for i in self.atms}
        active = [a for a in self.atms.values() if a["lifecycle"] != "maintenance"]
        empty_24 = sum(1 for i, r in recs.items() if r and r["hours_to_empty"] is not None and r["hours_to_empty"] <= 24)
        visits_today = sum(1 for r in recs.values() if r and r["urgency"] in ("urgent", "today"))
        freed = sum(r["freed_cash"] for r in recs.values() if r)
        return {
            "now": SIM_NOW.isoformat(sep=" "),
            "atms_total": len(self.atms),
            "atms_online": sum(1 for a in self.atms.values() if a["online"]),
            "cash_in_network": sum(a["balance"] for a in self.atms.values()),
            "empty_within_24h": empty_24,
            "visits_today": visits_today,
            "no_cash_tx_7d": sum(a["no_cash_7d"] for a in active),
            "tx_7d": sum(a["tx_7d"] for a in active),
            "freed_cash": freed,
            "capacity_limited": sum(1 for r in recs.values() if r and r["capacity_limited"]),
            "service_level": self.forecaster.service_level,
        }

    def detail(self, atm_id: str, cycle_h: int, rate_pct: float) -> Optional[dict]:
        a = self.atms.get(atm_id)
        if not a:
            return None
        rec = self.recommendation(atm_id, cycle_h, rate_pct)
        f = self.forecasts.get(atm_id)
        conn = self._connect()
        hist = conn.execute(
            "SELECT ts, balance, demand_amount, dispensed_amount, incassation, online FROM hourly "
            "WHERE atm_id=? AND ts>? ORDER BY ts",
            (atm_id, (pd.Timestamp(SIM_NOW) - pd.Timedelta(days=7)).isoformat(sep=" ")),
        ).fetchall()
        incs = conn.execute(
            "SELECT ts, kind, loaded_amount, returned_amount FROM incassations WHERE atm_id=? "
            "ORDER BY ts DESC LIMIT 8", (atm_id,),
        ).fetchall()
        conn.close()
        return {
            "atm": {k: v for k, v in a.items()},
            "recommendation": rec,
            "explanation": self.forecaster.explain(f) if f is not None else [],
            "demand_forecast": None if f is None else {
                "ts": [t.isoformat(sep=" ") for t in f["ts"]],
                "expected": [int(x) for x in f["pred"]],
            },
            "history": [dict(r) for r in hist],
            "incassations": [dict(r) for r in incs],
        }

    def transactions(self, atm_id: str, page: int, page_size: int,
                     status: str = "all", q: str = "") -> dict:
        where = ["atm_id=?"]
        args: List = [atm_id]
        if status != "all":
            where.append("status=?")
            args.append(status)
        if q:
            where.append("(card_masked LIKE ? OR card_token LIKE ? OR tx_id LIKE ?)")
            args += [f"%{q}%"] * 3
        w = " AND ".join(where)
        conn = self._connect()
        total = conn.execute(f"SELECT COUNT(*) FROM transactions WHERE {w}", args).fetchone()[0]
        pages = max(1, -(-total // page_size))
        page = min(max(page, 1), pages)
        rows = conn.execute(
            f"SELECT * FROM transactions WHERE {w} ORDER BY ts DESC LIMIT ? OFFSET ?",
            args + [page_size, (page - 1) * page_size],
        ).fetchall()
        agg = conn.execute(
            "SELECT status, COUNT(*), SUM(amount) FROM transactions WHERE atm_id=? GROUP BY status",
            (atm_id,),
        ).fetchall()
        conn.close()
        a = self.atms[atm_id]
        return {
            "page": page, "pages": pages, "total": total, "page_size": page_size,
            "items": [
                dict(r) | {"address": a["address"], "lat": a["lat"], "lon": a["lon"]}
                for r in rows
            ],
            "by_status": {r[0]: {"count": r[1], "amount": r[2]} for r in agg},
        }


service = AnalyticsService()


# ── Совместимость со страницей «Аналитика ATM» (dashboard/analytics.html) ──

PERIOD_HOURS = {"12h": 12, "24h": 24, "7d": 168, "30d": 720}
PERIOD_LABELS = {"12h": "12 часов", "24h": "24 часа", "7d": "7 дней", "30d": "1 месяц"}


def _risk_label(prob: float) -> str:
    return "HIGH" if prob >= 0.5 else "MEDIUM" if prob >= 0.2 else "LOW"


def network_period(svc: AnalyticsService, period: str) -> dict:
    hours = PERIOD_HOURS[period]
    since = (pd.Timestamp(SIM_NOW) - pd.Timedelta(hours=hours)).isoformat(sep=" ")
    conn = svc._connect()
    rows = conn.execute(
        "SELECT atm_id, SUM(dispensed_amount), SUM(loaded_amount), SUM(incassation), "
        "SUM(n_no_cash), AVG(balance) FROM hourly WHERE ts>=? GROUP BY atm_id",
        (since,),
    ).fetchall()
    conn.close()

    demand, risk = [], []
    total_out = total_in = inc = no_cash = 0
    prevented_n = 0
    pct_list = []
    for atm_id, out, loaded, n_inc, n_nc, avg_bal in rows:
        a = svc.atms.get(atm_id)
        if not a:
            continue
        std = a["standard_load_value"] or 1
        avg_pct = round((avg_bal or 0) / std * 100, 1)
        pct_list.append(avg_pct)
        total_out += out or 0
        total_in += loaded or 0
        inc += n_inc or 0
        no_cash += n_nc or 0
        demand.append({
            "atm_id": atm_id, "name": a["address"], "outcome": int(out or 0),
            "income": int(loaded or 0), "avg_balance_pct": avg_pct, "incassations": int(n_inc or 0),
        })
        f = svc.forecasts.get(atm_id)
        prob = svc.forecaster.cashout_prob_24h(f, a["balance"]) if f is not None else 0.0
        risk.append({
            "atm_id": atm_id, "name": a["address"],
            "balance_pct": round(a["balance"] / std * 100, 1),
            "cashout_prob": prob, "risk_label": _risk_label(prob),
        })
    # предотвращённый cash-out: визит, когда остаток час назад был ниже 20% стандартной загрузки точки
    conn = svc._connect()
    for a in svc.atms.values():
        prevented_n += conn.execute(
            "SELECT COUNT(*) FROM hourly h JOIN hourly p ON p.atm_id=h.atm_id "
            "AND p.ts=strftime('%Y-%m-%d %H:%M:%S', datetime(h.ts, '-1 hour')) "
            "WHERE h.atm_id=? AND h.ts>=? AND h.incassation=1 AND p.balance < ?",
            (a["atm_id"], since, 0.2 * a["standard_load_value"]),
        ).fetchone()[0]
    conn.close()

    demand.sort(key=lambda x: -x["outcome"])
    risk.sort(key=lambda x: -x["cashout_prob"])
    return {
        "period": period,
        "label": PERIOD_LABELS[period],
        "summary": {
            "total_outcome": int(total_out),
            "total_income": int(total_in),
            "net_cash_flow": int(total_in - total_out),
            "incassations": int(inc),
            "cashout_events": int(no_cash),
            "prevented_cashouts": int(prevented_n),
            "avg_balance_pct": round(sum(pct_list) / max(len(pct_list), 1), 1),
            "high_risk_atms": sum(1 for r in risk if r["risk_label"] == "HIGH"),
            "medium_risk_atms": sum(1 for r in risk if r["risk_label"] == "MEDIUM"),
        },
        "top_demand": demand[:8],
        "top_risk": risk[:8],
        "data": "synthetic",
    }


def transaction_history(svc: AnalyticsService, page: int, page_size: int, atm_id: Optional[str]) -> dict:
    where, args = ("WHERE atm_id=?", [atm_id]) if atm_id else ("", [])
    conn = svc._connect()
    total = conn.execute(f"SELECT COUNT(*) FROM transactions {where}", args).fetchone()[0]
    pages = max(1, -(-total // page_size))
    page = min(max(page, 1), pages)
    rows = conn.execute(
        f"SELECT ts, atm_id, amount, status FROM transactions {where} ORDER BY ts DESC LIMIT ? OFFSET ?",
        args + [page_size, (page - 1) * page_size],
    ).fetchall()
    conn.close()
    labels = {"ok": "Успешно", "no_cash": "Нет наличных", "declined": "Отказ эмитента"}
    items = []
    for ts, aid, amount, status in rows:
        a = svc.atms.get(aid, {})
        std = a.get("standard_load_value") or 1
        items.append({
            "timestamp": ts, "atm_id": aid, "atm_name": a.get("address", aid),
            "type": "Снятие наличных", "amount": int(amount) if status == "ok" else 0,
            "balance": a.get("balance", 0), "balance_pct": round(a.get("balance", 0) / std * 100, 1),
            "status": labels.get(status, status),
        })
    return {"page": page, "page_size": page_size, "total": total, "total_pages": pages, "items": items}
