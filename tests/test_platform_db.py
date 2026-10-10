from api.platform.db import _apply_live_state


def _live(balance=300_000_000, polled_at="2026-10-10T13:00:00+00:00"):
    return {"T1": {"balance": balance, "agent_status": "ONLINE", "forecast_hours": 40.0, "polled_at": polled_at}}


CASSETTES = {"T1": [{"nominal": 100_000, "count": 1000}]}


def test_monitoring_overrides_older_manual_balance():
    d = {"terminal_id": "T1", "balance": 50_000_000, "last_balance_at": "2026-10-10 12:00:00"}
    _apply_live_state(d, _live(), CASSETTES)
    assert d["balance"] == 300_000_000
    assert d["balance_source"] == "monitoring"
    assert d["forecast_hours"] == 40.0
    assert d["cassettes"] == CASSETTES["T1"]


def test_newer_manual_balance_wins_and_drops_stale_snapshot():
    d = {"terminal_id": "T1", "balance": 50_000_000, "last_balance_at": "2026-10-10 14:00:00"}
    _apply_live_state(d, _live(), CASSETTES)
    assert d["balance"] == 50_000_000
    assert d["balance_source"] == "manual"
    assert d["forecast_hours"] is None
    assert d["cassettes"] == []
    assert d["live"] is True


def test_no_manual_balance_uses_monitoring():
    d = {"terminal_id": "T1", "balance": None, "last_balance_at": None}
    _apply_live_state(d, _live(), CASSETTES)
    assert d["balance"] == 300_000_000 and d["balance_source"] == "monitoring"
