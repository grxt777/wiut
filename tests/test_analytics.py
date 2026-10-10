"""Юнит-тесты модуля аналитики банкоматов (без сборки данных)."""

import numpy as np
import pandas as pd

from api.analytics.config import ATM_MAX_CASH_UZS, DENOMS, SIM_NOW
from api.analytics.forecast import allocate_bills, recommend
from api.analytics.generator import (
    _card_masked, _greedy_available, _mix_bills, _schedule_visit, _standard_load,
)
from api.analytics.registry import build_registry


def test_mix_bills_sum_to_amount():
    amounts = np.array([50_000, 380_000, 1_000_000, 3_990_000], dtype=np.int64)
    bills = _mix_bills(amounts)
    assert ((bills * np.array(DENOMS)).sum(axis=1) == amounts).all()


def test_greedy_respects_availability():
    out = _greedy_available(300_000, np.array([0, 10, 0, 0]))
    assert out is not None and out[1] == 6
    assert _greedy_available(300_000, np.array([0, 1, 0, 0])) is None


def test_scheduled_visit_moves_sunday_to_saturday_once():
    from datetime import datetime
    rng = np.random.default_rng(0)
    fri, sat, sun = datetime(2026, 10, 9, 12), datetime(2026, 10, 10, 13), datetime(2026, 10, 11, 12)
    assert _schedule_visit(sun, fri, rng).weekday() == 5
    # визит уже был в субботу — следующий не возвращается в ту же субботу, а ждёт понедельника
    assert _schedule_visit(sun, sat, rng).weekday() == 0


def test_card_is_masked_never_full_pan():
    masked, token = _card_masked(123_456, 0)
    assert masked.startswith("8600 ") and "•" in masked
    assert sum(ch.isdigit() for ch in masked) == 8
    assert token.startswith("tok_")


def test_registry_is_deterministic_and_scored():
    a, b = build_registry(), build_registry()
    assert [x["atm_id"] for x in a] == [x["atm_id"] for x in b]
    assert all(0 <= x["geo_score"] <= 10 for x in a)
    assert len({x["atm_id"] for x in a}) == len(a)


def _fc(hourly_demand: float):
    ts = pd.date_range(SIM_NOW, periods=168, freq="h")
    return {"ts": ts, "pred": np.full(168, hourly_demand)}


def test_recommend_urgent_when_cash_runs_out():
    rec = recommend(
        _fc(10_000_000), balance=50_000_000, cassettes=[0, 400, 150, 0],
        denom_shares=[0.05, 0.35, 0.3, 0.3], lo=np.full(168, 0.8), hi=np.full(168, 1.2),
        now=pd.Timestamp(SIM_NOW), standard_load_value=380_000_000,
    )
    assert rec["hours_to_empty"] == 5
    assert rec["urgency"] == "urgent"
    assert rec["first_cassette_empty"]["already_empty"]
    assert all(b % 100 == 0 and b <= 2_500 for b in rec["load"]["bills"])


def test_recommend_ok_when_plenty_of_cash():
    rec = recommend(
        _fc(100_000), balance=400_000_000, cassettes=[1000, 1800, 1200, 900],
        denom_shares=[0.05, 0.35, 0.3, 0.3], lo=np.full(168, 0.8), hi=np.full(168, 1.2),
        now=pd.Timestamp(SIM_NOW), standard_load_value=380_000_000,
    )
    assert rec["urgency"] == "ok" and rec["visit_by"] is None
    assert rec["freed_cash"] > 0


def _value(bills):
    return sum(b * d for b, d in zip(bills, DENOMS))


def test_allocate_bills_covers_need_and_redistributes_capped_cassette():
    # 100k-кассете приходится 90% суммы — она упирается в 2500 купюр (250 млн), остаток уходит в другие
    need = 380_000_000
    bills = allocate_bills(need, [0.02, 0.04, 0.9, 0.04])
    assert bills[2] == 2_500
    assert need <= _value(bills) <= ATM_MAX_CASH_UZS
    assert all(b % 100 == 0 and b <= 2_500 for b in bills)


def test_allocate_bills_never_exceeds_atm_capacity():
    for need in (395_000_000, 399_999_999, 400_000_000, 600_000_000, 2_000_000_000):
        for shares in ([0.05, 0.35, 0.3, 0.3], [0.02, 0.04, 0.04, 0.9], [0.25] * 4):
            bills = allocate_bills(need, shares)
            assert _value(bills) <= ATM_MAX_CASH_UZS
            assert _value(bills) >= min(need, ATM_MAX_CASH_UZS) - 10_000_000
            assert all(b % 100 == 0 and b <= 2_500 for b in bills)


def test_standard_load_fits_atm_capacity():
    for score in np.linspace(0, 10, 41):
        assert 0 < _value(_standard_load(score)) <= ATM_MAX_CASH_UZS


def test_recommend_flags_capacity_limit():
    # спрос 30 млн/ч → за 72 ч ≈ 2.2 млрд, больше предела банкомата (400 млн)
    rec = recommend(
        _fc(30_000_000), balance=396_000_000, cassettes=[600, 1600, 1200, 950],
        denom_shares=[0.05, 0.35, 0.3, 0.3], lo=np.full(168, 0.8), hi=np.full(168, 1.2),
        now=pd.Timestamp(SIM_NOW), standard_load_value=380_000_000,
    )
    assert ATM_MAX_CASH_UZS - 10_000_000 <= rec["load"]["value"] <= ATM_MAX_CASH_UZS
    assert rec["capacity_limited"]
    assert 0 < rec["covers_h"] < rec["cycle_h"]


def test_topup_brings_atm_to_load_level_without_overfilling():
    for balance, demand in ((287_700_000, 4_000_000), (5_000_000, 3_000_000), (390_000_000, 30_000_000)):
        rec = recommend(
            _fc(demand), balance=balance, cassettes=[600, 1600, 1200, 950],
            denom_shares=[0.05, 0.35, 0.3, 0.3], lo=np.full(168, 0.8), hi=np.full(168, 1.2),
            now=pd.Timestamp(SIM_NOW), standard_load_value=380_000_000,
        )
        assert 0 <= rec["balance_at_visit"] <= balance
        assert rec["topup_value"] == max(rec["load"]["value"] - rec["balance_at_visit"], 0)
        assert rec["balance_at_visit"] + rec["topup_value"] <= ATM_MAX_CASH_UZS


def test_recommend_uses_service_level_bound():
    common = dict(
        balance=200_000_000, cassettes=[500, 1000, 800, 500], denom_shares=[0.05, 0.35, 0.3, 0.3],
        lo=np.full(168, 0.8), hi=np.full(168, 1.2), now=pd.Timestamp(SIM_NOW),
        standard_load_value=380_000_000,
    )
    p90 = recommend(_fc(2_000_000), **common)
    p99 = recommend(_fc(2_000_000), **common, safe=np.full(168, 1.5), service_level=0.99)
    assert p90["service_level"] == 0.9 and p99["service_level"] == 0.99
    assert p99["demand_cycle_safe"] > p90["demand_cycle_safe"]
    assert p99["load"]["value"] >= p90["load"]["value"]
    # более консервативная граница не может сдвинуть срок визита позже
    assert p99["visit_by"] <= p90["visit_by"]
    assert not p99["capacity_limited"] and p99["covers_h"] == p99["cycle_h"]


def test_geo_probe_matches_atm_score_and_grid_is_in_city():
    from api.analytics.geo_map import GeoMap, grade
    from api.analytics.registry import BANK_NAME
    atms = {a["atm_id"]: a for a in build_registry()}
    gm = GeoMap(atms, BANK_NAME)
    for a in list(atms.values())[:10]:
        p = gm.probe(a["lat"], a["lon"])
        assert abs(p["geo_score"] - a["geo_score"]) <= 0.05
    g = gm.grid(600)
    assert g["n"] > 100 and max(g["layers"]["score"]) <= 10
    assert grade(7.2) == "Отличная" and grade(1.0) == "Плохая"
