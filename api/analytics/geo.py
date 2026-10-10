"""
Гео-скоринг локации банкомата (0.00–10.00) по открытым данным.

Потенциал точки — взвешенная сумма мест скопления людей из OpenStreetMap
(метро, рынки, ТЦ, супермаркеты) с экспоненциальным затуханием по расстоянию:
potential = 10 · (1 − exp(−raw / s)), s подобран так, что медианная точка сети = 5.8.
Банкоматы других банков (реестр ЦБ РУз) делят тот же спрос, поэтому
слегка снижают оценку: score = max(0, potential − 0.3 · ln(1 + n в 500 м)).

Это прозрачная формула (веса — допущение команды, вынесены в константы),
а не обученная модель: открытых данных о реальных снятиях нет. На пилоте веса
калибруются по фактическим транзакциям банка.
"""

from __future__ import annotations

import json
import math
from functools import lru_cache
from typing import Dict, List

from .config import BASE_DIR

SOURCES_DIR = BASE_DIR / "data_sources"
ATMS_PATH = SOURCES_DIR / "cbu_atms_tashkent.json"
DISTRICTS_PATH = BASE_DIR / "tashkent_districts.geojson"
SPOTS_PATH = SOURCES_DIR / "osm_spots_tashkent.json"

# Вес категории места и радиус влияния
POI_WEIGHTS = {"metro": 3.0, "market": 2.6, "mall": 2.0, "supermarket": 0.8}
# Потенциал медианной точки сети (до штрафа за конкурентов): места спроса поднимают оценку
POTENTIAL_MEDIAN = 5.8
DECAY_M = 300.0          # расстояние, на котором вклад падает в e раз
MAX_RADIUS_M = 1_200.0
COMPETITOR_RADIUS_M = 500.0
# Штраф за конкуренцию в баллах оценки: 0.3 · ln(1 + n) — 5 банкоматов ≈ −0.54, 20 ≈ −0.91, 41 ≈ −1.12
COMPETITOR_PENALTY = 0.3

LABELS = {
    "metro": "Метро", "market": "Рынок", "mall": "ТЦ",
    "supermarket": "Супермаркет", "competitors": "Банкоматы других банков",
}


def haversine_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    r = 6_371_000.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


@lru_cache(maxsize=1)
def load_spots() -> List[dict]:
    data = json.loads(SPOTS_PATH.read_text(encoding="utf-8"))
    return [
        {"cat": s["cat"], "name": s.get("name") or "", "lat": s["pt"][0], "lon": s["pt"][1]}
        for s in data["spots"] if s.get("pt")
    ]


@lru_cache(maxsize=1)
def load_cbu_atms() -> List[dict]:
    return json.loads(ATMS_PATH.read_text(encoding="utf-8"))["atms"]


def _polygons(geom: dict) -> List[list]:
    t = geom["type"]
    if t == "Polygon":
        return [geom["coordinates"]]
    if t == "MultiPolygon":
        return list(geom["coordinates"])
    if t == "GeometryCollection":
        return [p for g in geom["geometries"] for p in _polygons(g)]
    return []


@lru_cache(maxsize=1)
def load_districts() -> List[tuple]:
    data = json.loads(DISTRICTS_PATH.read_text(encoding="utf-8"))
    return [(f["properties"]["ADM2_RU"], _polygons(f["geometry"])) for f in data["features"]]


def _in_ring(lon: float, lat: float, ring: list) -> bool:
    inside = False
    j = len(ring) - 1
    for i in range(len(ring)):
        xi, yi = ring[i][0], ring[i][1]
        xj, yj = ring[j][0], ring[j][1]
        if (yi > lat) != (yj > lat) and lon < (xj - xi) * (lat - yi) / (yj - yi) + xi:
            inside = not inside
        j = i
    return inside


def district_of(lat: float, lon: float) -> str | None:
    """Район Ташкента по координатам (полигоны tashkent_districts.geojson)."""
    for name, polys in load_districts():
        for poly in polys:
            if _in_ring(lon, lat, poly[0]) and not any(_in_ring(lon, lat, h) for h in poly[1:]):
                return name
    return None


def raw_index(lat: float, lon: float, own_bank: str) -> dict:
    by_cat: Dict[str, float] = {k: 0.0 for k in POI_WEIGHTS}
    nearest: Dict[str, dict] = {}
    for s in load_spots():
        if abs(s["lat"] - lat) > 0.012 or abs(s["lon"] - lon) > 0.016:
            continue
        d = haversine_m(lat, lon, s["lat"], s["lon"])
        if d > MAX_RADIUS_M:
            continue
        by_cat[s["cat"]] += POI_WEIGHTS[s["cat"]] * math.exp(-d / DECAY_M)
        if s["cat"] not in nearest or d < nearest[s["cat"]]["dist_m"]:
            nearest[s["cat"]] = {"name": s["name"], "dist_m": round(d)}

    competitors = sum(
        1 for a in load_cbu_atms()
        if a["b"] != own_bank and a.get("lat")
        and abs(a["lat"] - lat) < 0.006 and abs(a["lng"] - lon) < 0.008
        and haversine_m(lat, lon, a["lat"], a["lng"]) <= COMPETITOR_RADIUS_M
    )
    return {
        "by_cat": by_cat,
        "competitors_500m": competitors,
        "penalty": COMPETITOR_PENALTY * math.log1p(competitors),
        "raw": sum(by_cat.values()),
        "nearest": nearest,
    }


def potential_scale(median_raw: float) -> float:
    """s, при котором 10 · (1 − e^(−median/s)) = POTENTIAL_MEDIAN."""
    return (median_raw or 1.0) / -math.log(1 - POTENTIAL_MEDIAN / 10)


def explain(by_cat: Dict[str, float], potential: float, penalty: float, competitors: int) -> List[dict]:
    """Разбор оценки: вклад мест спроса в баллах (+) и штраф за конкурентов (−)."""
    total = sum(by_cat.values()) or 1.0
    out = [
        {"factor": k, "label": LABELS[k], "share_pct": round(v / total * 100, 1),
         "points": round(potential * v / total, 2)}
        for k, v in sorted(by_cat.items(), key=lambda kv: -kv[1]) if v > 0
    ]
    if competitors:
        out.append({"factor": "competitors", "label": LABELS["competitors"], "share_pct": None,
                    "points": -round(penalty, 2), "count": int(competitors)})
    return out


def score_points(points: List[dict], own_bank: str) -> List[dict]:
    """points: [{lat, lon}] → [{geo_score, dominant, breakdown...}] в том же порядке."""
    raws = [raw_index(p["lat"], p["lon"], own_bank) for p in points]
    vals = sorted(r["raw"] for r in raws)
    scale = potential_scale(vals[len(vals) // 2])
    out = []
    for r in raws:
        potential = 10 * (1 - math.exp(-r["raw"] / scale))
        score = round(max(0.0, potential - r["penalty"]), 2)
        total = sum(r["by_cat"].values()) or 1.0
        top_cat, top_val = max(r["by_cat"].items(), key=lambda kv: kv[1])
        dominant = top_cat if top_val >= 0.40 * total and top_val >= 0.6 else None
        out.append({
            "geo_score": score,
            "geo_potential": round(potential, 2),
            "competition_penalty": round(r["penalty"], 2),
            "dominant_poi": dominant,
            "competitors_500m": r["competitors_500m"],
            "geo_breakdown": explain(r["by_cat"], potential, r["penalty"], r["competitors_500m"]),
            "nearest_poi": r["nearest"],
        })
    return out


def competitors_near(own_points: List[dict], own_bank: str, radius_m: float = 50.0) -> List[dict]:
    """Банкоматы других банков (реестр ЦБ РУз) в радиусе radius_m от любого нашего банкомата."""
    deg_lat = radius_m / 111_000 * 1.2
    deg_lon = radius_m / 83_000 * 1.2
    found: Dict[str, dict] = {}
    for c in load_cbu_atms():
        if c["b"] == own_bank or not c.get("lat") or not c.get("lng"):
            continue
        for p in own_points:
            if abs(c["lat"] - p["lat"]) > deg_lat or abs(c["lng"] - p["lon"]) > deg_lon:
                continue
            d = haversine_m(p["lat"], p["lon"], c["lat"], c["lng"])
            if d <= radius_m:
                prev = found.get(c["id"])
                if prev is None or d < prev["dist_m"]:
                    found[c["id"]] = {
                        "id": str(c["id"]), "bank": c["b"], "address": c["a"],
                        "lat": c["lat"], "lon": c["lng"],
                        "near_atm_id": p["atm_id"], "dist_m": round(d),
                    }
    return sorted(found.values(), key=lambda x: (x["bank"], x["id"]))
