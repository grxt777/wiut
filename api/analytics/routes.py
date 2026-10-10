"""REST API страницы «Аналитика банкоматов»."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query

from .config import COST_OF_CAPITAL_PCT, DEFAULT_CYCLE_H
from .geo import competitors_near
from .registry import BANK_NAME
from .service import network_period, service, transaction_history

router = APIRouter(prefix="/api/analytics", tags=["analytics"])
# Эндпоинты страницы «Аналитика ATM» (dashboard/analytics.html) — без префикса
compat_router = APIRouter(tags=["analytics"])


def _ready():
    if not service.ready:
        raise HTTPException(503, "Аналитика ещё загружается")


@router.get("/summary", summary="Сводные показатели сети")
def summary():
    _ready()
    return service.summary()


@router.get("/atms", summary="Список банкоматов с прогнозом")
def atms(
    tab: str = Query("active", pattern="^(active|pilot|maintenance|all)$"),
    conn: str = Query("all", pattern="^(online|offline|all)$"),
    q: str = Query("", max_length=80),
    sort: str = Query("hours_to_empty", pattern="^(hours_to_empty|balance|geo_score|load_value|atm_id|address)$"),
    order: str = Query("asc", pattern="^(asc|desc)$"),
):
    _ready()
    return service.list_atms(tab=tab, conn=conn, q=q, sort=sort, order=order)


@router.get("/atms/{atm_id}", summary="Карточка банкомата: кассеты, прогноз, рекомендация")
def atm_detail(
    atm_id: str,
    cycle_h: int = Query(DEFAULT_CYCLE_H, ge=24, le=168),
    rate_pct: float = Query(COST_OF_CAPITAL_PCT, ge=0, le=60),
):
    _ready()
    d = service.detail(atm_id, cycle_h, rate_pct)
    if d is None:
        raise HTTPException(404, f"Банкомат {atm_id} не найден")
    return d


@router.get("/atms/{atm_id}/transactions", summary="Транзакции банкомата")
def atm_transactions(
    atm_id: str,
    page: int = Query(1, ge=1),
    page_size: int = Query(25, ge=10, le=100),
    status: str = Query("all", pattern="^(all|ok|no_cash|declined)$"),
    q: str = Query("", max_length=40),
):
    _ready()
    if atm_id not in service.atms:
        raise HTTPException(404, f"Банкомат {atm_id} не найден")
    return service.transactions(atm_id, page, page_size, status, q)


@router.get("/model", summary="Качество модели на тестовой выборке")
def model_metrics():
    _ready()
    return service.metrics


@compat_router.get("/api/analytics", summary="Операционная аналитика сети за период")
def analytics_period(period: str = Query("24h", pattern="^(12h|24h|7d|30d)$")):
    _ready()
    return network_period(service, period)


@compat_router.get("/api/transactions/history", summary="История транзакций сети с пагинацией")
def transactions_history(
    page: int = Query(1, ge=1),
    page_size: int = Query(12, ge=5, le=50),
    atm_id: str = Query(None),
):
    _ready()
    return transaction_history(service, page, page_size, atm_id)


@compat_router.get("/api/competitors", summary="Банкоматы других банков рядом с нашими (реестр ЦБ РУз)")
def competitors(radius_m: float = Query(50, ge=10, le=500)):
    _ready()
    own = [{"atm_id": a["atm_id"], "lat": a["lat"], "lon": a["lon"]} for a in service.atms.values()]
    items = competitors_near(own, BANK_NAME, radius_m)
    return {"radius_m": radius_m, "count": len(items), "items": items,
            "source": "Открытый реестр банкоматов ЦБ РУз (cbu.uz), 10.10.2026"}


# ── Гео-скоринг на карте ──
from .geo import load_spots  # noqa: E402
from .geo_map import LAYERS, GeoMap  # noqa: E402

_geo_map = None


def _geo() -> GeoMap:
    global _geo_map
    _ready()
    if _geo_map is None:
        _geo_map = GeoMap(service.atms, BANK_NAME)
    return _geo_map


@compat_router.get("/api/geo/atms", summary="Гео-скоринг банкоматов сети: оценка, разбор факторов, ранг")
def geo_atms():
    return _geo().atms_payload()


@compat_router.get("/api/geo/grid", summary="Тепловая карта гео-спроса по г. Ташкенту")
def geo_grid(step_m: int = Query(200, ge=150, le=1000)):
    g = _geo().grid(step_m)
    return {k: v for k, v in g.items() if not k.startswith("_")} | {"available_layers": LAYERS}


@compat_router.get("/api/geo/probe", summary="Оценка любой точки карты: скоринг и почему")
def geo_probe(lat: float = Query(..., ge=40.9, le=41.6), lon: float = Query(..., ge=68.9, le=69.7)):
    return _geo().probe(lat, lon)


@compat_router.get("/api/geo/opportunities", summary="«Белые пятна»: сильные локации без нашего банкомата рядом")
def geo_opportunities(min_gap_m: int = Query(700, ge=300, le=3000), top: int = Query(12, ge=1, le=40)):
    return {"min_gap_m": min_gap_m, "items": _geo().opportunities(min_gap_m=min_gap_m, top=top)}


@compat_router.get("/api/geo/pois", summary="Места скопления людей (OSM) для слоёв карты")
def geo_pois():
    return {"items": load_spots(), "source": "OpenStreetMap (ODbL), октябрь 2026"}
