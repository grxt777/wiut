from api.platform.incassation_router import (
    build_regional_routes,
    canonical_region,
    hours_to_low_cash,
    km,
    nearest_neighbor,
    needs_visit,
    optimize_tour,
    osrm_route_geometry,
)
from api.platform.ml_bridge import ml_severity


def test_canonical_region_viloyat_and_city():
    assert canonical_region("Самарқанд вилояти") == "Самарқанд вилояти"
    assert canonical_region("Самарқанд шаҳри") == "Самарқанд вилояти"
    assert canonical_region("Нарпай тумани", "140100 Самарқанд вилояти, Нарпай тумани") == "Самарқанд вилояти"
    assert canonical_region("Жиззах шаҳри") == "Жиззах вилояти"
    assert canonical_region("Янгиер шаҳри") == "Сирдарё вилояти"
    assert canonical_region("Юнусобод тумани", "100000 Тошкент шаҳри, Юнусобод тумани") == "Тошкент шаҳри"
    assert canonical_region("Чирчиқ шаҳар", "110700 Тошкент вилояти, Чирчиқ шаҳар") == "Тошкент вилояти"
    assert canonical_region("Шаҳрисабз шаҳри", "181300 Қашқадарё вилояти, Шаҳрисабз шаҳри") == "Қашқадарё вилояти"


def test_samarkand_not_served_from_jizzakh():
    sam_branch = {
        "id": 1, "local_code": "S1", "incassation": 1,
        "region": "Самарқанд шаҳри",
        "address": "140100 Самарқанд вилояти, Самарқанд шаҳри",
        "lat": 39.6542, "lon": 66.9597,
    }
    jiz_branch = {
        "id": 2, "local_code": "J1", "incassation": 1,
        "region": "Жиззах шаҳри",
        "address": "130100 Жиззах вилояти, Жиззах шаҳри",
        "lat": 40.1158, "lon": 67.8422,
    }
    atms = [
        {"terminal_id": "A1", "region": "Самарқанд вилояти", "lat": 39.66, "lon": 66.96, "status": "unknown"},
        {"terminal_id": "A2", "region": "Самарқанд вилояти", "lat": 39.67, "lon": 66.97, "status": "unknown"},
        {"terminal_id": "A3", "region": "Самарқанд вилояти", "lat": 39.64, "lon": 66.94, "status": "unknown"},
        {"terminal_id": "B1", "region": "Жиззах вилояти", "lat": 40.12, "lon": 67.84, "status": "unknown"},
    ]
    result = build_regional_routes(atms, [sam_branch, jiz_branch], status="all", snap_roads=False)
    by_region = {}
    for car in result["cars"]:
        by_region.setdefault(car["region"], []).append(car)
        codes = {s["terminal_id"] for s in car["stops"]}
        if car["region"] == "Самарқанд вилояти":
            assert car["departure_branch"]["local_code"] == "S1"
            assert codes <= {"A1", "A2", "A3"}
            assert "B1" not in codes
        if car["region"] == "Жиззах вилояти":
            assert car["departure_branch"]["local_code"] == "J1"
            assert codes == {"B1"}
    assert "Самарқанд вилояти" in by_region
    assert "Жиззах вилояти" in by_region


def test_three_samarkand_branches_only_cover_samarkand_atms():
    branches = [
        {"id": i, "local_code": f"S{i}", "incassation": 1,
         "region": "Самарқанд шаҳри",
         "address": "Самарқанд вилояти, Самарқанд шаҳри",
         "lat": 39.65 + i * 0.01, "lon": 66.95 + i * 0.01}
        for i in range(1, 4)
    ]
    atms = [
        {"terminal_id": f"ATM{i}", "region": "Самарқанд вилояти",
         "lat": 39.655 + i * 0.008, "lon": 66.955 + i * 0.008, "status": "unknown"}
        for i in range(5)
    ]
    # neighbouring viloyat must not steal these ATMs
    branches.append({
        "id": 99, "local_code": "JZ", "incassation": 1,
        "region": "Жиззах шаҳри", "address": "Жиззах вилояти",
        "lat": 39.70, "lon": 67.10,
    })
    result = build_regional_routes(atms, branches, status="all", snap_roads=False)
    codes = {c["departure_branch"]["local_code"] for c in result["cars"]}
    assert "JZ" not in codes
    assert codes <= {"S1", "S2", "S3"}
    served = {s["terminal_id"] for c in result["cars"] for s in c["stops"]}
    assert served == {f"ATM{i}" for i in range(5)}


def test_two_opt_not_worse_than_nn():
    depot = {"lat": 41.3, "lon": 69.28}
    points = [
        {"lat": 41.31, "lon": 69.30},
        {"lat": 41.35, "lon": 69.22},
        {"lat": 41.28, "lon": 69.35},
        {"lat": 41.33, "lon": 69.25},
        {"lat": 41.27, "lon": 69.27},
    ]
    nn = nearest_neighbor(depot, points)
    opt = optimize_tour(depot, points)

    def closed(seq):
        d = km(depot, seq[0])
        for i in range(len(seq) - 1):
            d += km(seq[i], seq[i + 1])
        return d + km(seq[-1], depot)

    assert closed(opt) <= closed(nn) + 1e-6


def test_warning_skips_ok_and_unknown():
    branch = {
        "id": 1, "local_code": "S1", "incassation": 1,
        "region": "Самарқанд вилояти", "address": "Самарқанд вилояти",
        "lat": 39.65, "lon": 66.96,
    }
    atms = [
        {"terminal_id": "OK1", "region": "Самарқанд вилояти", "lat": 39.66, "lon": 66.96, "status": "ok"},
        {"terminal_id": "W1", "region": "Самарқанд вилояти", "lat": 39.67, "lon": 66.97, "status": "warning"},
        {"terminal_id": "C1", "region": "Самарқанд вилояти", "lat": 39.64, "lon": 66.94, "status": "critical"},
        {"terminal_id": "U1", "region": "Самарқанд вилояти", "lat": 39.63, "lon": 66.93, "status": "unknown"},
    ]
    result = build_regional_routes(atms, [branch], status="warning", snap_roads=False)
    served = {s["terminal_id"] for c in result["cars"] for s in c["stops"]}
    assert served == {"W1", "C1"}
    assert result["diagnostics"]["target_atms"] == 2


def test_ml_decides_targets_and_priority():
    branch = {
        "id": 1, "local_code": "S1", "incassation": 1,
        "region": "Самарқанд вилояти", "address": "Самарқанд вилояти",
        "lat": 39.65, "lon": 66.96,
    }
    base = {"region": "Самарқанд вилояти", "lat": 39.66, "lon": 66.96, "capacity": 400_000_000}
    atms = [
        # остаток в норме, но ML видит опустошение — едем; довозим разницу до уровня ML
        {**base, "terminal_id": "ML_URGENT", "status": "ok", "balance": 300_000_000,
         "ml": {"urgency": "urgent", "visit_by": "2026-10-10 18:00:00", "load_value": 390_000_000,
                "balance_at_visit": 280_000_000, "topup_value": 110_000_000}},
        # остаток < 40%, но ML: хватит на неделю — не едем
        {**base, "terminal_id": "ML_PLANNED", "status": "warning", "balance": 150_000_000,
         "ml": {"urgency": "planned", "visit_by": "2026-10-14 09:00:00", "load_value": 200_000_000}},
        # остаток < 20% — страховка, едем даже при спокойном прогнозе
        {**base, "terminal_id": "CRIT_OK", "status": "critical", "balance": 50_000_000,
         "ml": {"urgency": "ok", "visit_by": None, "load_value": 100_000_000}},
        # на обслуживании — не едем
        {**base, "terminal_id": "MAINT", "status": "critical", "balance": 0,
         "ml": {"lifecycle": "maintenance", "urgency": None}},
        # без прогноза — пороги остатка
        {**base, "terminal_id": "NO_ML", "status": "warning", "balance": 150_000_000},
    ]
    result = build_regional_routes(atms, [branch], status="warning", snap_roads=False)
    stops = {s["terminal_id"]: s for c in result["cars"] for s in c["stops"]}
    assert set(stops) == {"ML_URGENT", "CRIT_OK", "NO_ML"}
    assert stops["ML_URGENT"]["priority"] == "critical"
    assert stops["ML_URGENT"]["refill_amount"] == 110_000_000
    assert stops["ML_URGENT"]["level_after"] == 390_000_000
    assert all(s["level_after"] <= 400_000_000 for s in stops.values())
    assert stops["ML_URGENT"]["forecast_source"] == "ml"
    assert stops["CRIT_OK"]["priority"] == "critical"
    assert stops["NO_ML"]["priority"] == "high"
    assert stops["NO_ML"]["forecast_source"] == "heuristic"
    assert result["diagnostics"]["skipped_maintenance"] == 1
    assert result["cars"][0]["priority"] == "critical"
    assert result["cars"][0]["deadline"] == "2026-10-10 18:00:00"


def test_needs_visit_levels():
    urgent = {"status": "ok", "ml": {"urgency": "urgent"}}
    today = {"status": "ok", "ml": {"urgency": "today"}}
    assert needs_visit(urgent, "critical") and needs_visit(urgent, "warning")
    assert not needs_visit(today, "critical") and needs_visit(today, "warning")
    assert needs_visit({"status": "critical"}, "critical")
    assert not needs_visit({"status": "warning"}, "critical")
    assert not needs_visit({"status": "ok", "ml": {"lifecycle": "maintenance"}}, "all")


def test_ml_severity_matches_visit_window():
    # суббота 18:00, до окна выезда (Пн 09:00) 39 ч
    base = {"urgency": "urgent", "next_window_h": 39}
    assert ml_severity({**base, "hours_to_empty": 30}) == "critical"   # опустеет до выезда
    assert ml_severity({**base, "hours_to_empty": 60}) == "warning"    # выезд нужен, но денег хватит
    assert ml_severity({"urgency": "planned", "next_window_h": 0, "hours_to_empty": 60}) == "ok"
    # будний день, окно открыто: критично — если опустеет в ближайшие сутки
    assert ml_severity({"urgency": "today", "next_window_h": 0, "hours_to_empty": 20}) == "critical"
    assert ml_severity({"urgency": "today", "next_window_h": 0, "hours_to_empty": 30}) == "warning"
    warn = {"status": "warning", "ml": {"urgency": "urgent", "severity": "warning"}}
    crit = {"status": "critical", "ml": {"urgency": "urgent", "severity": "critical"}}
    assert needs_visit(warn, "warning") and not needs_visit(warn, "critical")
    assert needs_visit(crit, "critical")


def test_hours_to_low_cash_none_without_balance():
    assert hours_to_low_cash({"capacity": 400_000_000}) is None


def test_hours_to_low_cash_critical_is_zero():
    hours = hours_to_low_cash({"capacity": 400_000_000, "balance": 40_000_000})
    assert hours == 0.0


def test_osrm_route_geometry_needs_two_valid_points():
    assert osrm_route_geometry([]) == (None, None)
    assert osrm_route_geometry([{"lat": 41.3, "lon": 69.2}]) == (None, None)
    assert osrm_route_geometry([{"lat": 0, "lon": 0}, {"lat": 0, "lon": 0}]) == (None, None)
