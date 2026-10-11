"""
ATM-платформа — FastAPI Backend
================================

Мониторинг банкоматов, прогноз спроса и инкассация в одном приложении.

  Реестр и мониторинг
    GET   /api/atms                         → список ATM из БД
    GET   /api/atms/stats                   → статистика по областям/филиалам
    GET   /api/atms/{terminal_id}           → детали ATM
    GET   /api/atms/{terminal_id}/cassettes → кассеты
    GET   /api/atms/{terminal_id}/history   → история остатка
    GET   /api/alerts, /api/baseline        → сигналы и сводка по остаткам
    POST  /api/routes/incassation           → региональные маршруты инкассации
    GET   /api/incassation/calendar         → календарь рейсов
    POST  /api/osrm/route                   → геометрия маршрута по дорогам

  Аналитика и прогноз (api/analytics)
    GET   /api/analytics/...                → прогноз XGBoost, рекомендации, транзакции

Docs: http://localhost:8000/docs
"""

from __future__ import annotations

import logging
import os
import sys
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # корень проекта: пакеты api, atm_monitor

import uvicorn
from fastapi import Body, FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from api.analytics.config import SIM_NOW
from api.analytics.routes import compat_router as analytics_compat_router, router as analytics_router
from api.analytics.service import service as analytics_service
from api.platform import atm_live
from api.platform.config import BASE_DIR
from api.platform.db import (
    DatabaseUnavailableError,
    bulk_update_balances,
    count_atms,
    count_branches,
    get_atm,
    get_branch,
    init_db,
    list_atms,
    list_branches,
    list_branches_full,
    list_incassation_trips,
    list_regions,
    replace_incassation_trips,
    update_balance,
)
from api.platform.incassation_router import (
    PRIORITY_RANK,
    _clean_osrm_points,
    build_regional_routes,
    hours_to_low_cash,
    needs_visit,
    osrm_route_geometry,
    stop_priority,
)
from api.platform.ml_bridge import attach_ml
from api.platform.seed import seed_if_empty

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(name)s  %(message)s",
)
log = logging.getLogger(__name__)
TASHKENT_TZ = timezone(timedelta(hours=5))


# ── Lifespan ─────────────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    import asyncio
    log.info("Инициализация БД платформы (SQLite)...")
    try:
        init_db()
    except DatabaseUnavailableError as e:
        print(str(e), file=sys.stderr, flush=True)
        logging.getLogger("uvicorn.error").setLevel(logging.CRITICAL)
        os._exit(1)
    loop = asyncio.get_event_loop()
    await loop.run_in_executor(None, analytics_service.load)
    seed_if_empty(analytics_service)
    # остатки, введённые вручную до перезапуска, — в прогноз аналитики
    attach_ml(list_atms(limit=5000), analytics_service)
    log.info("БД готова. ATM в базе: %d, филиалов: %d", count_atms(), count_branches())
    yield
    log.info("Сервер остановлен.")


# ── FastAPI App ──────────────────────────────────────────────

app = FastAPI(
    title="Cash Flow AI API",
    description="Мониторинг банкоматов, прогноз спроса на наличные и инкассация",
    version="4.0.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(analytics_router)
app.include_router(analytics_compat_router)

dashboard_dir = BASE_DIR / "dashboard"
if dashboard_dir.exists():
    app.mount("/dashboard", StaticFiles(directory=str(dashboard_dir), html=True), name="dashboard")


@app.get("/", include_in_schema=False)
async def root():
    return RedirectResponse(url="/dashboard/index.html")


@app.get("/analytics", include_in_schema=False)
async def analytics_page():
    return RedirectResponse(url="/dashboard/atm-analytics.html")


@app.get("/api/health", summary="Проверка живости сервиса")
async def health():
    return {
        "ok": True,
        "atms": count_atms(),
        "branches": count_branches(),
        "analytics_ready": analytics_service.ready,
        "modules": ["atm-monitor", "atm-analytics"],
    }

@app.middleware("http")
async def no_cache_dashboard(request: Request, call_next):
    """Страницы/скрипты дашборда браузер должен перепроверять каждый раз,
    иначе после обновления кода показывается старая закэшированная версия."""
    response = await call_next(request)
    path = request.url.path
    if path.startswith("/dashboard/") or path == "/":
        response.headers["Cache-Control"] = "no-cache"
    return response


# ═══════════════════════════════════════════════════════════
# ATM — СПИСОК / ДЕТАЛИ
# ═══════════════════════════════════════════════════════════

@app.get("/api/atms", summary="Список ATM (с фильтрами)")
async def get_atms(
    region: Optional[str] = Query(None, description="Область"),
    branch: Optional[str] = Query(None, description="Филиал"),
    status: Optional[str] = Query(None, pattern="^(ok|warning|critical|unknown)$"),
    limit: int = Query(1000, ge=1, le=5000),
    offset: int = Query(0, ge=0),
):
    rows = attach_ml(
        list_atms(region=region, branch=branch, limit=limit, offset=offset),
        analytics_service,
    )
    if status:
        rows = [r for r in rows if r["status"] == status]
    return {
        "atms": rows,
        "count": len(rows),
        "total_in_db": count_atms(),
        "filters": {"region": region, "branch": branch, "status": status},
    }


@app.get("/api/atms/stats", summary="Статистика по областям/филиалам")
async def get_stats():
    return {
        "total": count_atms(),
        "by_region": list_regions(),
        "by_branch": list_branches(),
    }


@app.get("/api/atms/{terminal_id}", summary="Детали ATM по Terminal ID")
async def get_atm_detail(terminal_id: str):
    atm = get_atm(terminal_id)
    if not atm:
        raise HTTPException(404, f"ATM с terminal_id={terminal_id!r} не найден")
    return attach_ml([atm], analytics_service)[0]


# ═══════════════════════════════════════════════════════════
# КАССЕТЫ + BASELINE
# ═══════════════════════════════════════════════════════════

CASSETTE_MAX_COUNT = 2000  # физический потолок купюр на одну кассету (оценка без реальных данных)


@app.get("/api/atms/{terminal_id}/cassettes", summary="Кассеты + рекомендация по загрузке")
async def get_atm_cassettes(terminal_id: str):
    """
    Если для ATM есть телеметрия кассет (atm_monitor.db) — отдаём её
    (номинал/количество/статус OK-LOW-MISSING на момент последнего снимка).
    Иначе — оценка: текущий остаток разбивается на 4 номинала пропорционально
    типичной доле в обороте (source="estimated", это не измерение).
    """
    atm = get_atm(terminal_id)
    if not atm:
        raise HTTPException(404, f"ATM {terminal_id!r} не найден")
    atm = attach_ml([atm], analytics_service)[0]

    # get_atm() уже подмешал реальные кассеты (см. core.db._apply_live_state);
    # отдельный запрос к atm_monitor тут не нужен.
    live_cassettes = atm.get("cassettes") or []
    if live_cassettes:
        return {
            "atm_id": terminal_id,
            "address": atm.get("address"),
            "region": atm.get("region"),
            "branch": atm.get("branch"),
            "current_balance": atm.get("balance"),
            "balance_polled_at": atm.get("balance_polled_at"),
            "source": "live",
            "cassettes": live_cassettes,
        }

    balance = atm.get("balance")
    capacity = atm.get("capacity") or 400_000_000

    if balance is None:
        return {
            "atm_id": terminal_id,
            "address": atm.get("address"),
            "region": atm.get("region"),
            "branch": atm.get("branch"),
            "current_balance": None,
            "capacity": capacity,
            "source": "estimated",
            "cassettes": {
                "denominations": [10_000, 50_000, 100_000, 200_000],
                "by_denom": {
                    str(d): {"count": 0, "balance": 0, "capacity": CASSETTE_MAX_COUNT, "fill_pct": 0}
                    for d in (10_000, 50_000, 100_000, 200_000)
                },
                "total_balance": 0,
                "total_fill_pct": 0,
                "value_to_fill": capacity,
            },
            "comment": "Баланс не загружен. Обновите через /api/atms/{id}/balance.",
        }

    # Оценка (нет реальных кассет от atm_monitor): раскладываем известный остаток
    # по 4 номиналам пропорционально типичной доле в обороте, но не больше
    # CASSETTE_MAX_COUNT купюр в одной кассете — это физический потолок реальной
    # кассеты. Итоговый баланс кассет — не сам исходный остаток, а честная сумма
    # «количество купюр × номинал» после этого ограничения.
    share = {10_000: 0.10, 50_000: 0.45, 100_000: 0.30, 200_000: 0.15}
    by_denom: Dict[str, Dict[str, Any]] = {}
    for d, s in share.items():
        alloc = int(balance * s)
        count = min(alloc // d, CASSETTE_MAX_COUNT)
        by_denom[str(d)] = {
            "count": int(count),
            "balance": int(count * d),
            "capacity": CASSETTE_MAX_COUNT,
            "fill_pct": round(count / CASSETTE_MAX_COUNT * 100, 1),
        }

    total_cassette_value = sum(int(c["balance"]) for c in by_denom.values())
    value_to_fill = max(0, capacity - total_cassette_value)

    return {
        "atm_id": terminal_id,
        "address": atm.get("address"),
        "region": atm.get("region"),
        "branch": atm.get("branch"),
        "current_balance": balance,
        "capacity": capacity,
        "balance_pct": atm.get("balance_pct"),
        "status": atm.get("status"),
        "source": "estimated",
        "cassettes": {
            "denominations": [10_000, 50_000, 100_000, 200_000],
            "by_denom": by_denom,
            "total_balance": total_cassette_value,
            "total_fill_pct": round(total_cassette_value / capacity * 100, 1) if capacity else 0,
            "value_to_fill": value_to_fill,
        },
        "refill_needed": value_to_fill,
    }


@app.get("/api/atms/{terminal_id}/history", summary="Реальная история остатка ATM (для прогноза)")
async def get_atm_history(
    terminal_id: str,
    days: int = Query(30, ge=1, le=180, description="Сколько дней истории отдать"),
):
    """
    Почасовой ряд остатка UZS из телеметрии atm_monitor.db.
    Пусто, если для этого ATM телеметрии нет.
    """
    atm = get_atm(terminal_id)
    live_tid = (atm.get("live_tid") if atm else None) or terminal_id
    history = atm_live.balance_history_by_tid(live_tid, days=days)
    return {"terminal_id": terminal_id, "days": days, "history": history, "count": len(history)}


# ═══════════════════════════════════════════════════════════
# ФИЛИАЛЫ
# ═══════════════════════════════════════════════════════════

@app.get("/api/branches", summary="Список филиалов (с фильтрами)")
async def get_branches(
    region: Optional[str] = Query(None, description="Регион"),
    incassation: Optional[int] = Query(None, ge=0, le=1, description="0 — без инкассации, 1 — с инкассацией"),
    limit: int = Query(2000, ge=1, le=5000),
    offset: int = Query(0, ge=0),
):
    rows = list_branches_full(region=region, incassation=incassation, limit=limit, offset=offset)
    return {
        "branches": rows,
        "count": len(rows),
        "total_in_db": count_branches(),
        "filters": {"region": region, "incassation": incassation},
    }


@app.get("/api/branches/stats", summary="Статистика по филиалам")
async def get_branches_stats():
    all_branches = list_branches_full(limit=5000)
    with_inc = sum(1 for b in all_branches if b.get("incassation") == 1)
    regions: Dict[str, int] = {}
    for b in all_branches:
        r = b.get("region") or "Не указан"
        regions[r] = regions.get(r, 0) + 1
    return {
        "total": count_branches(),
        "with_incassation": with_inc,
        "without_incassation": len(all_branches) - with_inc,
        "by_region": [{"region": r, "count": c} for r, c in sorted(regions.items(), key=lambda x: -x[1])],
    }


@app.get("/api/branches/{local_code}", summary="Детали филиала по локал коду")
async def get_branch_detail(local_code: str):
    branch = get_branch(local_code)
    if not branch:
        raise HTTPException(404, f"Филиал с local_code={local_code!r} не найден")
    return branch


# ═══════════════════════════════════════════════════════════
# БАЛАНСЫ
# ═══════════════════════════════════════════════════════════

class BalanceUpdate(BaseModel):
    balance: int


class BulkBalances(BaseModel):
    updates: List[Dict[str, Any]]   # [{"terminal_id": "...", "balance": 123}, ...]


@app.post("/api/atms/{terminal_id}/balance", summary="Обновить остаток ATM")
async def set_balance(terminal_id: str, payload: BalanceUpdate):
    atm = get_atm(terminal_id)
    if atm is None:
        raise HTTPException(404, f"ATM {terminal_id!r} не найден")
    if not 0 <= payload.balance <= atm["capacity"]:
        raise HTTPException(
            400, f"Остаток должен быть от 0 до {atm['capacity'] / 1e6:.0f} млн сум (ёмкость банкомата)",
        )
    update_balance(terminal_id, payload.balance)
    return {"ok": True, "atm": attach_ml([get_atm(terminal_id)], analytics_service)[0]}


@app.post("/api/atms/balances/bulk", summary="Массовое обновление остатков")
async def set_balances_bulk(payload: BulkBalances):
    result = bulk_update_balances(payload.updates)
    attach_ml(list_atms(limit=5000), analytics_service)
    return {"ok": True, **result}


# ═══════════════════════════════════════════════════════════
# ALERTS / BASELINE
# ═══════════════════════════════════════════════════════════

def _alert_reasons(a: Dict[str, Any]) -> List[str]:
    reasons = []
    ml = a.get("ml") or {}
    if ml.get("urgency") == "urgent":
        reasons.append("ML: выезд в ближайшие 6 ч")
    elif ml.get("urgency") == "today":
        reasons.append("ML: выезд сегодня")
    balance_status = a.get("balance_status") or a.get("status")
    if balance_status == "critical":
        reasons.append("Остаток < 20%")
    elif balance_status == "warning" and not ml.get("urgency"):
        reasons.append("Остаток < 40% (прогноза нет)")
    if ml.get("severity") == "critical" and balance_status != "critical":
        reasons.append(f"ML: деньги закончатся через ~{ml.get('hours_to_empty')} ч")
    if ml.get("capacity_limited"):
        reasons.append(f"Спрос за цикл больше 400 млн — полной загрузки хватит на ~{ml.get('covers_h')} ч")
    return reasons


@app.get("/api/alerts", summary="ATM, которым нужен выезд инкассации (ML-срок или порог остатка)")
async def get_alerts(limit: int = Query(200, ge=1, le=2000)):
    atms = attach_ml(list_atms(limit=5000), analytics_service)
    alerts = [a for a in atms if needs_visit(a, "warning")]
    for a in alerts:
        a["priority"] = stop_priority(a)
        a["alert_reasons"] = _alert_reasons(a)
    alerts.sort(key=lambda x: (
        PRIORITY_RANK[x["priority"]],
        (x.get("ml") or {}).get("visit_by") or "9999",
        x.get("balance_pct") if x.get("balance_pct") is not None else 999,
    ))
    return {"alerts": alerts[:limit], "count": len(alerts)}


def _aggregate_by_region(atms: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    agg: Dict[str, Dict[str, Any]] = {}
    for a in atms:
        r = a.get("region") or "Не указана"
        bucket = agg.setdefault(r, {
            "region": r, "count": 0, "total_capacity": 0, "total_balance": 0,
            "critical": 0, "warning": 0, "ok": 0, "no_data": 0,
        })
        bucket["count"] += 1
        bucket["total_capacity"] += a.get("capacity") or 0
        bucket["total_balance"] += a.get("balance") or 0
        st = a.get("status")
        if st in ("critical", "warning", "ok"):
            bucket[st] += 1
        else:
            bucket["no_data"] += 1
    out = []
    for r in agg.values():
        r["fill_pct"] = round(r["total_balance"] / (r["total_capacity"] or 1) * 100, 1)
        out.append(r)
    out.sort(key=lambda x: x["count"], reverse=True)
    return out


@app.get("/api/baseline", summary="Сводный отчёт по остаткам")
async def get_baseline():
    all_atms = attach_ml(list_atms(limit=5000), analytics_service)
    total_capacity = sum(a.get("capacity") or 0 for a in all_atms)
    total_balance = sum(a.get("balance") or 0 for a in all_atms)
    return {
        "summary": {
            "total_atms": len(all_atms),
            "total_capacity": total_capacity,
            "total_balance": total_balance,
            "fill_pct": round(total_balance / total_capacity * 100, 1) if total_capacity else 0,
            "no_data": sum(1 for a in all_atms if a.get("balance") is None),
            "critical": sum(1 for a in all_atms if a.get("status") == "critical"),
            "warning": sum(1 for a in all_atms if a.get("status") == "warning"),
            "ok": sum(1 for a in all_atms if a.get("status") == "ok"),
        },
        "by_region": _aggregate_by_region(all_atms),
    }


# ═══════════════════════════════════════════════════════════
# ИНКАССАЦИЯ
# ═══════════════════════════════════════════════════════════

class IncassationRouteRequest(BaseModel):
    persist: bool = True


@app.post("/api/routes/incassation", summary="Маршруты: ATM, которым нужен выезд (ML / остаток), свой вилоят")
async def regional_incassation_route(
    status: str = Query("warning", pattern="^(critical|warning|all)$"),
    speed_kmh: int = Query(30, ge=10, le=80),
    max_stops: int = Query(12, ge=1, le=200),
    snap_roads: bool = Query(True),
    payload: Optional[IncassationRouteRequest] = Body(None),
):
    req = payload or IncassationRouteRequest()
    # остаток из atm_monitor / ручного ввода (см. platform.db) + ML-срок визита и загрузка
    atms = attach_ml(list_atms(limit=5000), analytics_service)
    branches = list_branches_full(incassation=1, limit=5000)
    result = build_regional_routes(
        atms, branches, status,
        speed_kmh=speed_kmh, max_stops=max_stops, snap_roads=snap_roads,
    )
    if req.persist:
        result["saved_to_calendar"] = replace_incassation_trips(result.get("cars") or [])
    else:
        result["saved_to_calendar"] = 0
    return result


@app.get("/api/incassation/calendar", summary="Сохранённые рейсы инкассации для календаря")
async def incassation_calendar():
    trips = list_incassation_trips()
    events = []
    for t in trips:
        due = f"{t.get('planned_date')}T09:00:00+05:00"
        deadline = min((s["visit_by"] for s in t.get("stops") or [] if s.get("visit_by")), default=None)
        events.append({
            "deadline": deadline,
            "late": bool(deadline and (t.get("planned_date") or "") > deadline[:10]),
            "id": t.get("id"),
            "due_at": due,
            "planned_date": t.get("planned_date"),
            "region": t.get("region"),
            "address": t.get("label") or t.get("branch_address"),
            "terminal_id": t.get("branch_local_code"),
            "priority": t.get("priority") or "planned",
            "recommended_refill": t.get("refill_total") or 0,
            "stops": t.get("stops") or [],
            "stop_count": len(t.get("stops") or []),
            "distance_km": t.get("distance_km"),
            "est_time_min": t.get("est_time_min"),
            "label": t.get("label"),
            "branch_address": t.get("branch_address"),
            "branch_lat": t.get("branch_lat"),
            "branch_lon": t.get("branch_lon"),
            "geometry": t.get("geometry") or [],
            "type": "route",
        })
    return {"events": events, "count": len(events), "trips": trips}


@app.get("/api/incassation/trips/{trip_id}", summary="Один сохранённый рейс для карты")
async def incassation_trip(trip_id: int):
    for t in list_incassation_trips():
        if int(t.get("id") or 0) == int(trip_id):
            return t
    raise HTTPException(404, f"Рейс {trip_id} не найден")


class OsrmRouteRequest(BaseModel):
    points: List[Dict[str, float]]


@app.post("/api/osrm/route", summary="Геометрия маршрута по дорогам (OSRM)")
async def osrm_route(payload: OsrmRouteRequest):
    if len(_clean_osrm_points(payload.points)) < 2:
        raise HTTPException(400, "Нужно минимум 2 точки с корректными координатами")
    geom, dist = osrm_route_geometry(payload.points)
    if not geom:
        raise HTTPException(502, "OSRM недоступен")
    return {"geometry": geom, "distance_km": round(float(dist or 0), 2), "provider": "osrm"}


@app.get("/api/incassation/plan", summary="Прогнозный план инкассации")
async def incassation_plan(days: int = Query(2, ge=1, le=14)):
    atms = attach_ml(list_atms(limit=5000), analytics_service)
    now = datetime.now(timezone.utc)
    # прогноз ML построен на момент последних данных (SIM_NOW, местное время Ташкента)
    as_of = SIM_NOW.replace(tzinfo=TASHKENT_TZ)
    horizon = days * 24
    planned = []
    for a in atms:
        ml = a.get("ml") or {}
        if ml.get("lifecycle") == "maintenance":
            continue
        if ml.get("urgency"):
            if not ml.get("visit_by"):
                continue
            due = datetime.fromisoformat(ml["visit_by"]).replace(tzinfo=TASHKENT_TZ)
            hours = round((due - as_of) / timedelta(hours=1), 1)
            refill = int(ml["topup_value"])
            source = "ml"
        else:
            hours = hours_to_low_cash(a)
            if hours is None:
                continue
            due = now + timedelta(hours=hours)
            cap, bal = a.get("capacity") or 400_000_000, a.get("balance")
            refill = round(max(0, cap * 0.8 - (bal or 0)))
            source = "heuristic"
        if hours > horizon:
            continue
        planned.append({
            "terminal_id": a["terminal_id"],
            "address": a.get("address"),
            "region": a.get("region"),
            "due_at": due.astimezone(TASHKENT_TZ).isoformat(timespec="minutes"),
            "hours_to_visit": hours,
            "hours_to_low_cash": hours,
            "hours_to_empty": ml.get("hours_to_empty") if source == "ml" else hours,
            "priority": stop_priority(a),
            "recommended_refill": refill,
            "capacity_limited": bool(ml.get("capacity_limited")),
            "forecast_source": source,
        })
    planned.sort(key=lambda x: (x["due_at"], PRIORITY_RANK[x["priority"]]))
    return {
        "generated_at": now.isoformat(),
        "forecast_as_of": as_of.isoformat(),
        "horizon_days": days,
        "planned_atms": planned,
        "count": len(planned),
        "note": (
            "Срок визита и загрузка — ML (XGBoost, уровень сервиса P"
            f"{round(analytics_service.forecaster.service_level * 100) if analytics_service.ready else 99}); "
            "ATM без прогноза — оценка по текущему остатку. Календарь рейсов — POST /api/routes/incassation."
        ),
    }


# ═══════════════════════════════════════════════════════════
# GEOJSON
# ═══════════════════════════════════════════════════════════

def _geojson_response(name: str) -> FileResponse:
    geo_path = BASE_DIR / name
    if not geo_path.exists():
        raise HTTPException(404, "GeoJSON не найден")
    return FileResponse(str(geo_path), media_type="application/geo+json")


@app.get("/tashkent_city.geojson", summary="Граница г. Ташкента (контур для карты)")
async def get_tashkent_city_geojson():
    return _geojson_response("tashkent_city.geojson")


@app.get("/tashkent_districts.geojson", summary="Границы районов Ташкента")
async def get_tashkent_geojson():
    return _geojson_response("tashkent_districts.geojson")


@app.get("/uzbekistan_regional.geojson", summary="Границы областей Узбекистана")
async def get_uzb_regional_geojson():
    return _geojson_response("uzbekistan_regional.geojson")


@app.get("/uzbekistan.geojson", summary="Граница Узбекистана")
async def get_uzb_geojson():
    return _geojson_response("uzbekistan.geojson")


# ═══════════════════════════════════════════════════════════
# ENTRY POINT
# ═══════════════════════════════════════════════════════════

if __name__ == "__main__":
    uvicorn.run("api.main:app", host="0.0.0.0", port=int(os.getenv("PORT", os.getenv("APP_PORT", "8000"))), reload=False, log_level="info")
