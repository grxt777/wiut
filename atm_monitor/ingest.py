import json
import logging
from datetime import datetime, timezone

from .db import connect
from .utils import get_path, parse_dt, parse_float, parse_int

logger = logging.getLogger("atm_monitor.ingest")


def _iso(dt):
    """datetime -> ISO-строка UTC (в SQLite время хранится текстом; сортируется корректно)."""
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).isoformat()


def _insert(conn, table: str, values: dict, upsert_on: str = None) -> int:
    cols = ", ".join(values)
    marks = ", ".join("?" for _ in values)
    sql = f"INSERT INTO {table} ({cols}) VALUES ({marks})"
    if upsert_on:
        upd = ", ".join(f"{c}=excluded.{c}" for c in values if c != upsert_on)
        sql += f" ON CONFLICT({upsert_on}) DO UPDATE SET {upd}"
    return conn.execute(sql, list(values.values())).lastrowid


def _upsert_atm_dimension(conn, item: dict, seen_at: str):
    card = item.get("card") or {}
    model = item.get("model") or {}
    gps = card.get("gpsCoords") or {}
    extra = card.get("extraAttrs") or {}

    _insert(
        conn,
        "atms",
        dict(
            id=item["id"],
            serial=item.get("serial"),
            tid=item.get("tid"),
            client_id=item.get("clientId"),
            model_id=item.get("modelId"),
            model_name=model.get("name"),
            vendor_name=get_path(model, "vendor", "name"),
            variant_name=get_path(model, "variant", "name"),
            type_name=get_path(model, "type", "name"),
            hw_uid=item.get("hwUid"),
            atm_uid=item.get("atmUid"),
            branch_number=card.get("branchNumber"),
            mfo=extra.get("mfo"),
            merchant_id=extra.get("merchantId"),
            terminal_id=extra.get("terminalId"),
            host_provider=extra.get("hostProvider"),
            country_id=card.get("countryId"),
            region_id=card.get("regionId"),
            city_id=card.get("cityId"),
            address=card.get("address"),
            place=card.get("place"),
            latitude=parse_float(gps.get("latitude")),
            longitude=parse_float(gps.get("longitude")),
            timezone_offset=card.get("timezoneOffset"),
            max_cashout=model.get("maxCashout"),
            max_cashin=model.get("maxCashin"),
            status=item.get("status"),
            api_created_at=_iso(parse_dt(item.get("createdAt"))),
            api_updated_at=_iso(parse_dt(item.get("updatedAt"))),
            last_seen_at=seen_at,
        ),
        upsert_on="id",
    )


def _save_snapshot(conn, item: dict, poll_run_id: int, polled_at: str):
    state = item.get("state") or {}
    agent = item.get("agentStatus") or {}
    device = item.get("deviceStatus") or {}
    cdm = item.get("cdmRemainingAmount") or {}
    ltt = item.get("lastTransactionTimestamp") or {}

    snapshot_id = _insert(
        conn,
        "atm_snapshots",
        dict(
            atm_id=item["id"],
            poll_run_id=poll_run_id,
            polled_at=polled_at,
            agent_status=agent.get("status"),
            agent_last_online=_iso(parse_dt(agent.get("lastOnline"))),
            service_status=state.get("serviceStatus"),
            app_status=state.get("appStatus"),
            app_conn_status=state.get("appConnStatus"),
            sup_switch_status=state.get("supSwitchStatus"),
            platform_status=state.get("platformStatus"),
            vdm_status=state.get("vdmStatus"),
            state_updated_at=_iso(parse_dt(state.get("updatedAt"))),
            last_transaction_last=_iso(parse_dt(ltt.get("last"))),
            last_transaction_cash_out=_iso(parse_dt(ltt.get("cashOut"))),
            last_transaction_cash_in=_iso(parse_dt(ltt.get("cashIn"))),
            last_transaction_other=_iso(parse_dt(ltt.get("other"))),
            cdm_total_uzs=parse_float(cdm.get("totalUzs")),
            cdm_total_usd=parse_float(cdm.get("totalUsd")),
            cdm_total_eur=parse_float(cdm.get("totalEur")),
            dispenser_status=device.get("dispenser"),
            acceptor_status=device.get("acceptor"),
            epp_status=device.get("epp"),
            card_reader_status=device.get("cardReader"),
            contactless_status=device.get("contactless"),
            printer_status=device.get("printer"),
            jprinter_status=device.get("jprinter"),
            barcode_reader_status=device.get("barcodeReader"),
            # Полный сырой объект — подстраховка на случай новых полей.
            raw_json=json.dumps(item, ensure_ascii=False),
        ),
    )

    for c in item.get("cdmCassetteStatusBrief") or []:
        _insert(
            conn,
            "cassette_snapshots",
            dict(
                atm_snapshot_id=snapshot_id,
                atm_id=item["id"],
                polled_at=polled_at,
                cassette_index=c.get("index"),
                unit_id=c.get("unitId"),
                cassette_type=c.get("type"),
                status=c.get("status"),
                count=parse_int(c.get("count")),
                currency=c.get("currency"),
                nominal=parse_int(c.get("values")),
            ),
        )

    for t in item.get("turnoverTotal") or []:
        _insert(
            conn,
            "turnover_snapshots",
            dict(
                atm_id=item["id"],
                polled_at=polled_at,
                device=t.get("device"),
                currency=t.get("currency"),
                balance=parse_float(t.get("balance")),
                dispense_amount_rate=parse_float(t.get("dispenseAmountRate")),
                present_amount_rate=parse_float(t.get("presentAmountRate")),
                forecast_hours=parse_float(t.get("forecast")),
            ),
        )


def ingest(items: list) -> int:
    """Сохраняет один опрос: апдейтит справочник atms и пишет историю
    (atm_snapshots / cassette_snapshots / turnover_snapshots). Возвращает id прогона.
    Весь опрос — одна транзакция; при сбое данные не пишутся, а в poll_runs
    остаётся запись со статусом failed."""

    polled_at = _iso(datetime.now(timezone.utc))

    try:
        with connect() as conn:
            poll_run_id = _insert(
                conn, "poll_runs",
                dict(started_at=polled_at, status="running", atm_count=len(items)),
            )
            for item in items:
                if "id" not in item:
                    logger.warning("Пропускаем запись без id: %s", item)
                    continue
                _upsert_atm_dimension(conn, item, polled_at)
                _save_snapshot(conn, item, poll_run_id, polled_at)
            conn.execute(
                "UPDATE poll_runs SET status='success', finished_at=? WHERE id=?",
                (_iso(datetime.now(timezone.utc)), poll_run_id),
            )
        return poll_run_id
    except Exception as e:
        with connect() as conn:
            _insert(
                conn, "poll_runs",
                dict(started_at=polled_at, finished_at=_iso(datetime.now(timezone.utc)),
                     status="failed", atm_count=len(items), error_message=str(e)),
            )
        raise
