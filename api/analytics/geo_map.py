"""
Гео-скоринг на карте: та же формула, что в geo.py, но векторно — для сетки
(тепловая карта), произвольной точки (клик «оценить место») и поиска
«белых пятен» (сильные локации без нашего банкомата рядом).

Шкала 0–10 общая с реестром (тот же s, что в geo.score_points),
поэтому оценка клетки или точки напрямую сравнима с оценкой банкомата.
Оценка = потенциал мест спроса − штраф за банкоматы других банков.
"""

from __future__ import annotations

import json
import math
import sqlite3
from functools import lru_cache
from typing import Dict, List, Optional

import numpy as np

from .config import BASE_DIR, DB_PATH
from .geo import (
    COMPETITOR_PENALTY,
    COMPETITOR_RADIUS_M,
    DECAY_M,
    LABELS,
    MAX_RADIUS_M,
    POI_WEIGHTS,
    _in_ring,
    _polygons,
    district_of,
    explain,
    potential_scale,
    load_cbu_atms,
    load_spots,
)

CITY_PATH = BASE_DIR / "tashkent_city.geojson"
CATS = list(POI_WEIGHTS)
LAYERS = ["score", *CATS, "competitors"]

# Пороги качества локации (для легенды и подписей)
# (подобраны под распределение сети: медиана = 5.00, максимум ≈ 7.7)
GRADES = [
    (7.0, "Отличная"),
    (5.5, "Хорошая"),
    (4.0, "Средняя"),
    (2.5, "Слабая"),
    (0.0, "Плохая"),
]


def grade(score: float) -> str:
    for thr, label in GRADES:
        if score >= thr:
            return label
    return GRADES[-1][1]


def _xy(lat: np.ndarray, lon: np.ndarray, lat0: float) -> tuple:
    """Локальная равнопромежуточная проекция в метрах (точность < 0.5% на масштабе города)."""
    return lon * 111_320.0 * math.cos(math.radians(lat0)), lat * 110_540.0


@lru_cache(maxsize=1)
def _arrays():
    spots = load_spots()
    s_lat = np.array([s["lat"] for s in spots])
    s_lon = np.array([s["lon"] for s in spots])
    s_cat = np.array([CATS.index(s["cat"]) for s in spots])
    s_name = [s["name"] for s in spots]
    return s_lat, s_lon, s_cat, s_name


@lru_cache(maxsize=4)
def _competitor_arrays(own_bank: str):
    pts = [a for a in load_cbu_atms() if a["b"] != own_bank and a.get("lat") and a.get("lng")]
    return np.array([a["lat"] for a in pts]), np.array([a["lng"] for a in pts])


def components(lat: np.ndarray, lon: np.ndarray, own_bank: str) -> Dict[str, np.ndarray]:
    """Вклад каждого фактора в raw-индекс для массива точек (как в geo.raw_index)."""
    lat = np.atleast_1d(np.asarray(lat, dtype=float))
    lon = np.atleast_1d(np.asarray(lon, dtype=float))
    lat0 = float(lat.mean())
    s_lat, s_lon, s_cat, _ = _arrays()
    px, py = _xy(lat, lon, lat0)
    sx, sy = _xy(s_lat, s_lon, lat0)
    out = {c: np.zeros(len(lat)) for c in CATS}
    for i0 in range(0, len(lat), 2000):
        sl = slice(i0, i0 + 2000)
        d = np.hypot(px[sl, None] - sx[None, :], py[sl, None] - sy[None, :])
        w = np.where(d <= MAX_RADIUS_M, np.exp(-d / DECAY_M), 0.0)
        for ci, c in enumerate(CATS):
            out[c][sl] = POI_WEIGHTS[c] * w[:, s_cat == ci].sum(axis=1)
    c_lat, c_lon = _competitor_arrays(own_bank)
    cx, cy = _xy(c_lat, c_lon, lat0)
    n_comp = np.zeros(len(lat))
    for i0 in range(0, len(lat), 2000):
        sl = slice(i0, i0 + 2000)
        d = np.hypot(px[sl, None] - cx[None, :], py[sl, None] - cy[None, :])
        n_comp[sl] = (d <= COMPETITOR_RADIUS_M).sum(axis=1)
    out["_penalty"] = COMPETITOR_PENALTY * np.log1p(n_comp)
    out["_n_competitors"] = n_comp
    return out


def poi_raw(comp: Dict[str, np.ndarray]) -> np.ndarray:
    return sum(comp[c] for c in CATS)


def potential(raw: np.ndarray, scale: float) -> np.ndarray:
    return 10 * (1 - np.exp(-np.asarray(raw) / scale))


def to_score(comp: Dict[str, np.ndarray], scale: float) -> np.ndarray:
    """Оценка = потенциал мест спроса − штраф за конкурентов, не ниже 0."""
    return np.round(np.maximum(potential(poi_raw(comp), scale) - comp["_penalty"], 0.0), 2)


@lru_cache(maxsize=1)
def _city_polys() -> List[list]:
    data = json.loads(CITY_PATH.read_text(encoding="utf-8"))
    feats = data["features"] if "features" in data else [data]
    return [p for f in feats for p in _polygons(f["geometry"])]


def _in_ring_np(lon: np.ndarray, lat: np.ndarray, ring: list) -> np.ndarray:
    r = np.asarray(ring, dtype=float)
    xi, yi = r[:, 0], r[:, 1]
    xj, yj = np.roll(xi, 1), np.roll(yi, 1)
    inside = np.zeros(len(lon), dtype=bool)
    for a, b, c, d in zip(xi, yi, xj, yj):
        cond = (b > lat) != (d > lat)
        with np.errstate(divide="ignore", invalid="ignore"):
            x_cross = (c - a) * (lat - b) / (d - b) + a
        inside ^= cond & (lon < x_cross)
    return inside


def in_city_mask(lat: np.ndarray, lon: np.ndarray) -> np.ndarray:
    mask = np.zeros(len(lat), dtype=bool)
    for poly in _city_polys():
        m = _in_ring_np(lon, lat, poly[0])
        for hole in poly[1:]:
            m &= ~_in_ring_np(lon, lat, hole)
        mask |= m
    return mask


def in_city(lat: float, lon: float) -> bool:
    return any(
        _in_ring(lon, lat, poly[0]) and not any(_in_ring(lon, lat, h) for h in poly[1:])
        for poly in _city_polys()
    )


class GeoMap:
    def __init__(self, atms: Dict[str, dict], own_bank: str):
        self.own_bank = own_bank
        self.atms = atms
        ids = list(atms)
        lat = np.array([atms[i]["lat"] for i in ids])
        lon = np.array([atms[i]["lon"] for i in ids])
        comp = components(lat, lon, own_bank)
        raw = poi_raw(comp)
        self.scale = potential_scale(float(np.sort(raw)[len(raw) // 2]))
        self.atm_ids, self.atm_lat, self.atm_lon = ids, lat, lon
        self.atm_scores = np.array([atms[i]["geo_score"] for i in ids])
        self._daily = self._daily_demand()
        self._grid_cache: Dict[int, dict] = {}

    # ── спрос сети за 30 дней — для «сопоставимых банкоматов» ──
    def _daily_demand(self) -> Dict[str, float]:
        try:
            conn = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True)
            rows = conn.execute(
                "SELECT atm_id, SUM(demand_amount) * 1.0 / (COUNT(*) / 24.0) FROM hourly "
                "WHERE online = 1 AND ts >= (SELECT datetime(MAX(ts), '-30 days') FROM hourly) "
                "GROUP BY atm_id"
            ).fetchall()
            conn.close()
            return {a: float(v or 0) for a, v in rows}
        except sqlite3.Error:
            return {}

    def _comparable(self, score: float, exclude: Optional[str] = None) -> dict:
        pairs = [
            (abs(self.atms[i]["geo_score"] - score), self._daily.get(i))
            for i in self.atm_ids if i != exclude and self._daily.get(i)
        ]
        pairs.sort(key=lambda x: x[0])
        near = [v for _, v in pairs[:10]]
        return {
            "n": len(near),
            "median_daily_demand": int(np.median(near)) if near else None,
        }

    def percentile(self, score: float) -> float:
        return round(float((self.atm_scores < score).mean() * 100), 1)

    def _nearest_poi(self, lat: float, lon: float, k: int = 4) -> List[dict]:
        s_lat, s_lon, s_cat, s_name = _arrays()
        px, py = _xy(np.array([lat]), np.array([lon]), lat)
        sx, sy = _xy(s_lat, s_lon, lat)
        d = np.hypot(sx - px[0], sy - py[0])
        out = []
        for j in np.argsort(d)[:30]:
            if d[j] > MAX_RADIUS_M or len(out) >= k:
                break
            out.append({"cat": CATS[s_cat[j]], "label": LABELS[CATS[s_cat[j]]],
                        "name": s_name[j] or LABELS[CATS[s_cat[j]]], "dist_m": int(d[j]),
                        "lat": float(s_lat[j]), "lon": float(s_lon[j])})
        return out

    def _nearest_own(self, lat: float, lon: float) -> dict:
        px, py = _xy(np.array([lat]), np.array([lon]), lat)
        ax, ay = _xy(self.atm_lat, self.atm_lon, lat)
        d = np.hypot(ax - px[0], ay - py[0])
        j = int(np.argmin(d))
        return {"atm_id": self.atm_ids[j], "dist_m": int(d[j]),
                "geo_score": float(self.atm_scores[j])}

    def _explain(self, comp: Dict[str, np.ndarray], i: int) -> List[dict]:
        by_cat = {k: float(comp[k][i]) for k in CATS}
        pot = float(potential(np.array([sum(by_cat.values())]), self.scale)[0])
        return explain(by_cat, pot, float(comp["_penalty"][i]), int(comp["_n_competitors"][i]))

    # ── банкоматы сети ──
    def atms_payload(self) -> dict:
        comp = components(self.atm_lat, self.atm_lon, self.own_bank)
        items = []
        for i, aid in enumerate(self.atm_ids):
            a = self.atms[aid]
            score = float(a["geo_score"])
            items.append({
                "atm_id": aid,
                "lat": a["lat"], "lon": a["lon"],
                "address": a["address"], "district": a["district"],
                "category_label": a["category_label"],
                "geo_score": score, "grade": grade(score),
                "percentile": self.percentile(score),
                "rank": int((self.atm_scores > score).sum()) + 1,
                "breakdown": self._explain(comp, i),
                "competitors_500m": int(comp["_n_competitors"][i]),
                "nearest_poi": self._nearest_poi(a["lat"], a["lon"]),
                "daily_demand_30d": int(self._daily.get(aid, 0)) or None,
                "devices_at_site": a.get("devices_at_site", 1),
                "lifecycle": a.get("lifecycle"),
            })
        s = np.sort(self.atm_scores)
        return {
            "items": items,
            "stats": {
                "n": len(s), "min": float(s[0]), "median": float(np.median(s)), "max": float(s[-1]),
                "p25": float(np.percentile(s, 25)), "p75": float(np.percentile(s, 75)),
                "by_grade": {g: int(sum(1 for x in s if grade(x) == g)) for _, g in GRADES},
            },
            "grades": [{"min": t, "label": g} for t, g in GRADES],
            "formula": {
                "weights": POI_WEIGHTS, "decay_m": DECAY_M, "max_radius_m": MAX_RADIUS_M,
                "competitor_penalty": COMPETITOR_PENALTY, "competitor_radius_m": COMPETITOR_RADIUS_M,
                "scale": round(self.scale, 4),
            },
        }

    # ── сетка для тепловой карты ──
    def grid(self, step_m: int = 400) -> dict:
        if step_m in self._grid_cache:
            return self._grid_cache[step_m]
        polys = _city_polys()
        lons = [pt[0] for p in polys for pt in p[0]]
        lats = [pt[1] for p in polys for pt in p[0]]
        lat0 = (min(lats) + max(lats)) / 2
        dlat = step_m / 110_540.0
        dlon = step_m / (111_320.0 * math.cos(math.radians(lat0)))
        glat = np.arange(min(lats) + dlat / 2, max(lats), dlat)
        glon = np.arange(min(lons) + dlon / 2, max(lons), dlon)
        mlat, mlon = np.meshgrid(glat, glon, indexing="ij")
        mlat, mlon = mlat.ravel(), mlon.ravel()
        keep = in_city_mask(mlat, mlon)
        lat, lon = mlat[keep], mlon[keep]
        cells = lat
        comp = components(lat, lon, self.own_bank)
        raw = poi_raw(comp)
        layers = {"score": to_score(comp, self.scale)}
        for c in CATS:
            mx = float(comp[c].max()) or 1.0
            layers[c] = np.round(comp[c] / mx * 10, 2)   # 0–10 относительно максимума слоя
        dens = comp["_n_competitors"]
        layers["competitors"] = np.round(np.log1p(dens) / (np.log1p(dens.max()) or 1.0) * 10, 2)
        out = {
            "step_m": step_m, "dlat": dlat, "dlon": dlon, "n": len(cells),
            "lat": np.round(lat, 6).tolist(), "lon": np.round(lon, 6).tolist(),
            "layers": {k: v.tolist() for k, v in layers.items()},
            "_raw": raw,
        }
        self._grid_cache[step_m] = out
        return out

    # ── оценка произвольной точки ──
    def probe(self, lat: float, lon: float) -> dict:
        comp = components(np.array([lat]), np.array([lon]), self.own_bank)
        score = float(to_score(comp, self.scale)[0])
        return {
            "lat": lat, "lon": lon,
            "district": district_of(lat, lon),
            "in_city": in_city(lat, lon),
            "geo_score": score, "grade": grade(score),
            "percentile": self.percentile(score),
            "breakdown": self._explain(comp, 0),
            "competitors_500m": int(comp["_n_competitors"][0]),
            "nearest_poi": self._nearest_poi(lat, lon),
            "nearest_own_atm": self._nearest_own(lat, lon),
            "comparable": self._comparable(score),
        }

    # ── «белые пятна»: сильные клетки без нашего банкомата рядом ──
    def opportunities(self, min_gap_m: int = 700, top: int = 12, spacing_m: int = 1000) -> List[dict]:
        g = self.grid(300)
        lat, lon = np.array(g["lat"]), np.array(g["lon"])
        score = np.array(g["layers"]["score"])
        lat0 = float(lat.mean())
        px, py = _xy(lat, lon, lat0)
        ax, ay = _xy(self.atm_lat, self.atm_lon, lat0)
        d_own = np.sqrt(((px[:, None] - ax[None, :]) ** 2 + (py[:, None] - ay[None, :]) ** 2).min(axis=1))
        cand = np.nonzero(d_own >= min_gap_m)[0]
        cand = cand[np.argsort(-score[cand])]
        picked: List[int] = []
        for i in cand:
            if len(picked) >= top or score[i] < 5.0:
                break
            if all(math.hypot(px[i] - px[j], py[i] - py[j]) >= spacing_m for j in picked):
                picked.append(int(i))
        out = []
        for i in picked:
            p = self.probe(float(lat[i]), float(lon[i]))
            p["gap_to_own_m"] = int(d_own[i])
            out.append(p)
        return out
