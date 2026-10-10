"""
Реестр банкоматов.

Сеть — банкоматы Ipak Yo'li Bank в г. Ташкенте из открытого реестра ЦБ РУз
(data_sources/cbu_atms_tashkent.json): ID ЦБ, адрес, район, координаты,
платёжные системы. Серийные номера, модели, статусы жизненного цикла —
синтетические.

Гео-скоринг 0.00–10.00 и тип локации — по местам скопления людей из
OpenStreetMap и плотности банкоматов других банков (api/analytics/geo.py).
"""

from __future__ import annotations

import re
from collections import Counter
from datetime import timedelta
from typing import Dict, List

import numpy as np

from .config import SEED, SIM_NOW
from .geo import district_of, load_cbu_atms, score_points

BANK_NAME = "Ипак Йули банк"
BANK_LABEL = "Ipak Yo'li Bank"

CATEGORIES = {
    "metro":       {"label": "Метро"},
    "bozor":       {"label": "Рынок"},
    "mall":        {"label": "ТЦ"},
    "supermarket": {"label": "Супермаркет"},
    "bank_branch": {"label": "Офис банка"},
    "residential": {"label": "Жилой массив"},
}
_POI_TO_CATEGORY = {"metro": "metro", "market": "bozor", "mall": "mall", "supermarket": "supermarket"}

_VENDORS = [
    ("NCR", "SelfServ 22"),
    ("NCR", "SelfServ 23"),
    ("Hyosung", "MX8800"),
    ("GRG", "H68N"),
]
_VARIANTS = ["Лобби", "Уличный", "Встроенный"]

LIFECYCLE_LABELS = {
    "active":      "В работе",
    "pilot":       "Пилот",
    "maintenance": "Обслуживание",
}

# Сколько устройств на одном адресе считаем офисом банка
_BRANCH_DEVICES = 4


def _clean_address(raw: str) -> str:
    a = re.sub(r"^\d{6},\s*", "", raw.strip())
    a = re.sub(r"^г\.\s*Ташкент,\s*", "", a)
    return re.sub(r",\s*-\s*$", "", a)


_POI_NAMES = {"metro": "метро", "market": "рынок", "mall": "ТЦ", "supermarket": "супермаркет"}


def _landmark(nearest: dict) -> str | None:
    """Ближайший ориентир из OSM в радиусе 400 м: «метро Novza, 120 м»."""
    best = None
    for cat, p in nearest.items():
        if p["name"] and p["dist_m"] <= 400 and (best is None or p["dist_m"] < best[1]["dist_m"]):
            best = (cat, p)
    if not best:
        return None
    cat, p = best
    return f"{_POI_NAMES[cat]} «{p['name']}», {p['dist_m']} м"


def build_registry() -> List[Dict]:
    """Детерминированный реестр банкоматов (seed фиксирован)."""
    rng = np.random.default_rng(SEED)
    src = sorted(
        (a for a in load_cbu_atms() if a["b"] == BANK_NAME and a.get("lat") and a.get("lng")),
        key=lambda a: a["id"],
    )
    site_key = lambda a: (round(a["lat"], 5), round(a["lng"], 5))
    devices_at_site = Counter(site_key(a) for a in src)
    # Адрес, указанный у многих разных точек, — юридический адрес банка, а не место установки
    sites_per_address: Dict[str, set] = {}
    for a in src:
        sites_per_address.setdefault(a["a"], set()).add(site_key(a))
    geo = score_points([{"lat": a["lat"], "lon": a["lng"]} for a in src], BANK_NAME)

    n = len(src)
    pilot_idx = {int(n * 0.27), int(n * 0.82)}
    maintenance_idx = {int(n * 0.18), int(n * 0.64)}

    atms: List[Dict] = []
    for i, (a, g) in enumerate(zip(src, geo)):
        devices = devices_at_site[site_key(a)]
        if devices >= _BRANCH_DEVICES:
            profile = "bank_branch"
        elif g["dominant_poi"]:
            profile = _POI_TO_CATEGORY[g["dominant_poi"]]
        else:
            profile = "residential"

        district = district_of(a["lat"], a["lng"]) or a.get("d") or "Ташкент"
        legal_address = len(sites_per_address[a["a"]]) >= 3
        if legal_address:
            mark = _landmark(g["nearest_poi"])
            address = f"{district}, рядом: {mark}" if mark else f"{district}, {a['lat']:.5f}, {a['lng']:.5f}"
        else:
            address = _clean_address(a["a"])

        vendor, model = _VENDORS[int(rng.integers(len(_VENDORS)))]
        lifecycle = (
            "pilot" if i in pilot_idx
            else "maintenance" if i in maintenance_idx
            else "active"
        )
        installed_at = SIM_NOW - timedelta(days=21) if lifecycle == "pilot" else None
        maintenance_from = SIM_NOW - timedelta(days=3) if lifecycle == "maintenance" else None

        atms.append({
            "atm_id": str(a["id"]),
            "serial": f"SN-{int(rng.integers(10, 99))}-{int(rng.integers(10_000_000, 99_999_999))}",
            "bank": BANK_LABEL,
            "address": address,
            "registry_address": a["a"],
            "address_is_legal": legal_address,
            "district": district,
            "payment_systems": a.get("ps") or "",
            "lat": float(a["lat"]),
            "lon": float(a["lng"]),
            "category": profile,
            "category_label": CATEGORIES[profile]["label"],
            "geo_score": g["geo_score"],
            "geo_potential": g["geo_potential"],
            "competition_penalty": g["competition_penalty"],
            "score_source": "osm_poi",
            "geo_breakdown": g["geo_breakdown"],
            "nearest_poi": g["nearest_poi"],
            "competitors_500m": g["competitors_500m"],
            "devices_at_site": devices,
            "vendor": vendor,
            "model": model,
            "variant": _VARIANTS[int(rng.integers(len(_VARIANTS)))],
            "lifecycle": lifecycle,
            "installed_at": installed_at.isoformat(sep=" ") if installed_at else None,
            "maintenance_from": maintenance_from.isoformat(sep=" ") if maintenance_from else None,
        })
    return atms
