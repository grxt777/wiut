"""SQLite-хранилище сборщика atm_monitor (файл data/atm_monitor.db).

Раньше это была отдельная PostgreSQL; теперь — обычный файл, как и bank.db,
поэтому всё запускается одной командой без серверов БД."""
import sqlite3
from contextlib import contextmanager
from pathlib import Path

from .config import settings

SCHEMA = """
CREATE TABLE IF NOT EXISTS atms (
    id INTEGER PRIMARY KEY,
    serial TEXT, tid TEXT, client_id INTEGER,
    model_id INTEGER, model_name TEXT, vendor_name TEXT, variant_name TEXT, type_name TEXT,
    hw_uid TEXT, atm_uid TEXT,
    branch_number TEXT, mfo TEXT, merchant_id TEXT, terminal_id TEXT, host_provider TEXT,
    country_id INTEGER, region_id INTEGER, city_id INTEGER,
    address TEXT, place TEXT, latitude REAL, longitude REAL, timezone_offset INTEGER,
    max_cashout INTEGER, max_cashin INTEGER,
    status TEXT,
    api_created_at TEXT, api_updated_at TEXT,
    first_seen_at TEXT DEFAULT (datetime('now')),
    last_seen_at TEXT
);
CREATE INDEX IF NOT EXISTS ix_atms_tid ON atms(tid);

CREATE TABLE IF NOT EXISTS poll_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at TEXT NOT NULL, finished_at TEXT,
    status TEXT, atm_count INTEGER, error_message TEXT
);

CREATE TABLE IF NOT EXISTS atm_snapshots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    atm_id INTEGER NOT NULL REFERENCES atms(id),
    poll_run_id INTEGER REFERENCES poll_runs(id),
    polled_at TEXT NOT NULL,
    agent_status TEXT, agent_last_online TEXT,
    service_status TEXT, app_status TEXT, app_conn_status TEXT,
    sup_switch_status TEXT, platform_status TEXT, vdm_status TEXT, state_updated_at TEXT,
    last_transaction_last TEXT, last_transaction_cash_out TEXT,
    last_transaction_cash_in TEXT, last_transaction_other TEXT,
    cdm_total_uzs REAL, cdm_total_usd REAL, cdm_total_eur REAL,
    dispenser_status TEXT, acceptor_status TEXT, epp_status TEXT, card_reader_status TEXT,
    contactless_status TEXT, printer_status TEXT, jprinter_status TEXT, barcode_reader_status TEXT,
    raw_json TEXT
);
CREATE INDEX IF NOT EXISTS ix_snap_atm_time ON atm_snapshots(atm_id, polled_at);

CREATE TABLE IF NOT EXISTS cassette_snapshots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    atm_snapshot_id INTEGER REFERENCES atm_snapshots(id),
    atm_id INTEGER NOT NULL REFERENCES atms(id),
    polled_at TEXT NOT NULL,
    cassette_index INTEGER, unit_id TEXT, cassette_type TEXT, status TEXT,
    count INTEGER, currency TEXT, nominal INTEGER
);
CREATE INDEX IF NOT EXISTS ix_cass_snapshot ON cassette_snapshots(atm_snapshot_id);

CREATE TABLE IF NOT EXISTS turnover_snapshots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    atm_id INTEGER NOT NULL REFERENCES atms(id),
    polled_at TEXT NOT NULL,
    device TEXT, currency TEXT, balance REAL,
    dispense_amount_rate REAL, present_amount_rate REAL, forecast_hours REAL
);
CREATE INDEX IF NOT EXISTS ix_turn_atm_time ON turnover_snapshots(atm_id, polled_at);
"""


@contextmanager
def connect(readonly: bool = False):
    """Соединение с БД сборщика. Коммит при успехе, rollback при ошибке."""
    Path(settings.DB_PATH).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(settings.DB_PATH, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    try:
        yield conn
        if not readonly:
            conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_db():
    with connect() as conn:
        conn.executescript(SCHEMA)
