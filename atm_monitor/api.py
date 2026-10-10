"""API страницы «Менеджер банкоматов» (dashboard/atm-monitor.html): текущее
состояние каждого банкомата из БД сборщика (последний снимок) + ручной запуск опроса.
Подключается в api/main.py как router."""
import threading

from fastapi import APIRouter

from .db import connect
from .scheduler import run_once

router = APIRouter(prefix="/api/atm-monitor", tags=["atm-monitor"])


def _format_forecast(hours) -> "str | None":
    """~159.8 часов -> '~6 д 14 ч' (как на платформе-источнике)."""
    try:
        hours = float(hours)
    except (TypeError, ValueError):
        return None
    if hours <= 0:
        return None
    return f"~{int(hours // 24)} д {int(hours % 24)} ч"


@router.get("/atms", summary="Все банкоматы с последним снимком состояния")
def list_atms():
    try:
        with connect(readonly=True) as conn:
            atms = conn.execute("SELECT * FROM atms ORDER BY tid").fetchall()
            snaps = conn.execute(
                """
                SELECT * FROM atm_snapshots
                WHERE id IN (SELECT MAX(id) FROM atm_snapshots GROUP BY atm_id)
                """
            ).fetchall()
            snap_by_atm = {r["atm_id"]: r for r in snaps}

            cassettes: dict = {}
            for c in conn.execute(
                """
                SELECT * FROM cassette_snapshots
                WHERE atm_snapshot_id IN (SELECT MAX(id) FROM atm_snapshots GROUP BY atm_id)
                ORDER BY cassette_index
                """
            ).fetchall():
                cassettes.setdefault(c["atm_snapshot_id"], []).append(c)

            turnover = {
                r["atm_id"]: r
                for r in conn.execute(
                    """
                    SELECT * FROM turnover_snapshots
                    WHERE id IN (SELECT MAX(id) FROM turnover_snapshots GROUP BY atm_id)
                    """
                ).fetchall()
            }
    except Exception:  # noqa: BLE001 — база ещё не создана / пустая
        return []

    result = []
    for atm in atms:
        snap = snap_by_atm.get(atm["id"])
        t = turnover.get(atm["id"])
        result.append(
            {
                "id": atm["id"],
                "tid": atm["tid"],
                "serial": atm["serial"],
                "status": atm["status"],
                "vendor": atm["vendor_name"],
                "model": atm["model_name"],
                "variant": atm["variant_name"],
                "agent_status": snap["agent_status"] if snap else None,
                "cdm_total_uzs": snap["cdm_total_uzs"] if snap else None,
                "forecast": _format_forecast(t["forecast_hours"]) if t else None,
                "polled_at": snap["polled_at"] if snap else None,
                "cassettes": [
                    {
                        "index": c["cassette_index"],
                        "type": c["cassette_type"],
                        "status": c["status"],
                        "count": c["count"],
                        "nominal": c["nominal"],
                    }
                    for c in (cassettes.get(snap["id"], []) if snap else [])
                ],
            }
        )
    return result


@router.post("/poll", summary="Запустить опрос BTech прямо сейчас (в фоне)")
def poll_now():
    threading.Thread(target=run_once, name="atm-monitor-manual", daemon=True).start()
    return {"started": True}
