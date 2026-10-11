"""
Единый слой доступа к SQLite для Bank Intelligence Platform.

База — обычный файл `data/bank.db`, никаких серверов и внешних зависимостей.

Домен: atms / branches / incassation_trips — реестр банкоматов, филиалов
инкассации и сохранённые маршруты. Слой совместимости SQL (`%s`, `RETURNING id`
и т.п.) сохранён из исходного проекта integratsya.
"""

from __future__ import annotations

import json
import logging
import re
import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Any, Dict, Iterator, List, Optional

from . import atm_live
from .config import (
    DB_PATH,
    DATA_DIR,
    DEFAULT_CAPACITY,
    LOW_CASH_PCT,
    MAX_CAPACITY,
    WARNING_CASH_PCT,
)

log = logging.getLogger(__name__)

_write_lock = threading.Lock()


class DatabaseUnavailableError(RuntimeError):
    """Не удалось открыть файл базы данных."""


# ═══════════════════════════════════════════════════════════
# ПОДКЛЮЧЕНИЕ
# ═══════════════════════════════════════════════════════════

@contextmanager
def _connect() -> Iterator[sqlite3.Connection]:
    """Контекстный менеджер: соединение с SQLite и транзакция."""
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    try:
        conn = sqlite3.connect(str(DB_PATH), timeout=30, check_same_thread=False)
    except sqlite3.Error as e:
        raise DatabaseUnavailableError(
            f"\n❌ Не удалось открыть базу данных {DB_PATH}\n   Причина: {e}\n"
        ) from None

    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")   # параллельное чтение при записи

    # Встроенные LOWER/UPPER в SQLite работают только с латиницей: 'КАССИР'
    # остался бы в верхнем регистре, и фильтры по должности/филиалу на кириллице
    # не находили бы записи. Подменяем их Python-версиями с поддержкой Unicode.
    conn.create_function("lower", 1, lambda s: s.lower() if isinstance(s, str) else s)
    conn.create_function("upper", 1, lambda s: s.upper() if isinstance(s, str) else s)

    try:
        with _write_lock:
            yield conn
            conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


# ═══════════════════════════════════════════════════════════
# СЛОЙ СОВМЕСТИМОСТИ С POSTGRESQL (для модуля кассиров)
# ═══════════════════════════════════════════════════════════

_TRUNCATE_RE = re.compile(
    r"^\s*TRUNCATE\s+TABLE\s+(?P<tables>[\w\s,]+?)(\s+RESTART\s+IDENTITY)?(\s+CASCADE)?\s*$",
    re.IGNORECASE,
)
_ADD_COLUMN_RE = re.compile(
    r"^\s*ALTER\s+TABLE\s+(?P<table>\w+)\s+ADD\s+COLUMN\s+IF\s+NOT\s+EXISTS\s+(?P<column>\w+)\s+(?P<definition>.+?)\s*$",
    re.IGNORECASE | re.DOTALL,
)
_RETURNING_RE = re.compile(r"\s+RETURNING\s+id\s*$", re.IGNORECASE)


def _translate_sql(sql: str) -> str:
    """Переводит PostgreSQL-синтаксис в понятный SQLite."""
    sql = sql.replace("%s", "?")
    sql = re.sub(r"\bSERIAL\s+PRIMARY\s+KEY\b", "INTEGER PRIMARY KEY AUTOINCREMENT", sql, flags=re.I)
    sql = re.sub(r"\bBIGSERIAL\s+PRIMARY\s+KEY\b", "INTEGER PRIMARY KEY AUTOINCREMENT", sql, flags=re.I)
    sql = re.sub(r"\bTIMESTAMPTZ\b", "TEXT", sql, flags=re.I)
    sql = re.sub(r"\bDOUBLE\s+PRECISION\b", "REAL", sql, flags=re.I)
    sql = re.sub(r"\bBIGINT\b", "INTEGER", sql, flags=re.I)
    sql = re.sub(r"\bnow\(\)", "CURRENT_TIMESTAMP", sql, flags=re.I)
    return sql


class _CompatCursor:
    """Курсор с трансляцией PostgreSQL-запросов и dict-подобными строками."""

    def __init__(self, conn: sqlite3.Connection):
        self._conn = conn
        self._cur = conn.cursor()
        self._returning_id = False

    # ── основное ────────────────────────────────────────────
    def execute(self, sql: str, params: Any = ()) -> "_CompatCursor":
        self._returning_id = False

        # TRUNCATE TABLE a, b RESTART IDENTITY CASCADE
        m = _TRUNCATE_RE.match(sql)
        if m:
            tables = [t.strip() for t in m.group("tables").split(",") if t.strip()]
            for table in tables:
                self._cur.execute(f"DELETE FROM {table}")
            for table in tables:
                self._cur.execute("DELETE FROM sqlite_sequence WHERE name = ?", (table,))
            return self

        # ALTER TABLE ... ADD COLUMN IF NOT EXISTS ...
        m = _ADD_COLUMN_RE.match(sql)
        if m:
            table, column = m.group("table"), m.group("column")
            existing = {r["name"] for r in self._cur.execute(f"PRAGMA table_info({table})").fetchall()}
            if column not in existing:
                definition = _translate_sql(m.group("definition"))
                # SQLite не умеет NOT NULL DEFAULT в ADD COLUMN без константы —
                # значение по умолчанию здесь всегда константа, так что всё в порядке.
                self._cur.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")
            return self

        # INSERT ... RETURNING id  → используем lastrowid
        if _RETURNING_RE.search(sql):
            sql = _RETURNING_RE.sub("", sql)
            self._returning_id = True

        self._cur.execute(_translate_sql(sql), tuple(params) if params else ())
        return self

    def executemany(self, sql: str, seq: Any) -> "_CompatCursor":
        self._cur.executemany(_translate_sql(sql), seq)
        return self

    def fetchone(self):
        if self._returning_id:
            self._returning_id = False
            return {"id": self._cur.lastrowid}
        return self._cur.fetchone()

    def fetchall(self):
        return self._cur.fetchall()

    def close(self) -> None:
        self._cur.close()

    # ── совместимость ───────────────────────────────────────
    @property
    def rowcount(self) -> int:
        return self._cur.rowcount

    @property
    def lastrowid(self) -> int:
        return self._cur.lastrowid

    def __iter__(self):
        return iter(self._cur)


def dict_cursor(conn: sqlite3.Connection) -> _CompatCursor:
    """Курсор, возвращающий строки как словари (совместим с кодом на psycopg2)."""
    return _CompatCursor(conn)


# ═══════════════════════════════════════════════════════════
# ИНИЦИАЛИЗАЦИЯ СХЕМЫ
# ═══════════════════════════════════════════════════════════

def init_db() -> None:
    """Создаёт таблицы обоих доменов при первом запуске."""
    with _connect() as conn:
        cur = conn.cursor()

        cur.execute("""
            CREATE TABLE IF NOT EXISTS atms (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                terminal_id     TEXT UNIQUE NOT NULL,
                atm_number      TEXT,
                branch_code     TEXT,
                local_code      TEXT,
                region          TEXT,
                branch          TEXT,
                model           TEXT,
                network_type    TEXT,
                serial          TEXT,
                merchant_id     TEXT,
                address         TEXT,
                lat             REAL,
                lon             REAL,
                capacity        INTEGER NOT NULL DEFAULT 400000000,
                balance         INTEGER,
                last_balance_at TEXT,
                created_at      TEXT NOT NULL DEFAULT (datetime('now')),
                updated_at      TEXT NOT NULL DEFAULT (datetime('now'))
            )
        """)
        cur.execute(
            "UPDATE atms SET capacity = ? WHERE capacity IS NULL OR capacity <= 0 OR capacity > ?",
            (MAX_CAPACITY, MAX_CAPACITY),
        )
        cur.execute("CREATE INDEX IF NOT EXISTS idx_atms_region ON atms(region)")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_atms_branch ON atms(branch)")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_atms_local  ON atms(local_code)")

        cur.execute("""
            CREATE TABLE IF NOT EXISTS branches (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                number          TEXT,
                local_code      TEXT UNIQUE,
                region          TEXT,
                address         TEXT,
                lat             REAL,
                lon             REAL,
                incassation     INTEGER NOT NULL DEFAULT 0,
                created_at      TEXT NOT NULL DEFAULT (datetime('now')),
                updated_at      TEXT NOT NULL DEFAULT (datetime('now'))
            )
        """)
        cur.execute("CREATE INDEX IF NOT EXISTS idx_branches_region ON branches(region)")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_branches_inc    ON branches(incassation)")

        cur.execute("""
            CREATE TABLE IF NOT EXISTS incassation_trips (
                id                 INTEGER PRIMARY KEY AUTOINCREMENT,
                planned_date       TEXT NOT NULL,
                region             TEXT,
                label              TEXT,
                priority           TEXT,
                branch_local_code  TEXT,
                branch_address     TEXT,
                branch_lat         REAL,
                branch_lon         REAL,
                stops_json         TEXT NOT NULL DEFAULT '[]',
                geometry_json      TEXT NOT NULL DEFAULT '[]',
                distance_km        REAL,
                est_time_min       INTEGER,
                refill_total       INTEGER,
                generated_at       TEXT NOT NULL DEFAULT (datetime('now'))
            )
        """)
        cur.execute("CREATE INDEX IF NOT EXISTS idx_inc_trips_date ON incassation_trips(planned_date)")
        cur.close()

    log.info("База данных инициализирована: %s", DB_PATH)


# ═══════════════════════════════════════════════════════════
# ATM — ЧТЕНИЕ
# ═══════════════════════════════════════════════════════════

def _compute_status(balance: Optional[int], capacity: int) -> str:
    if balance is None:
        return "unknown"
    if capacity <= 0:
        return "ok"
    pct = balance / capacity
    if pct < LOW_CASH_PCT:
        return "critical"
    if pct < WARNING_CASH_PCT:
        return "warning"
    return "ok"


def _parse_utc(value: Any) -> Optional[datetime]:
    """'YYYY-MM-DD HH:MM:SS' из SQLite datetime('now') — UTC без зоны; ISO с зоной — как есть."""
    if not value:
        return None
    try:
        t = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return t if t.tzinfo else t.replace(tzinfo=timezone.utc)


def _apply_live_state(
    d: Dict[str, Any],
    live_by_tid: Dict[str, Dict[str, Any]],
    cassettes_by_tid: Optional[Dict[str, List[Dict[str, Any]]]] = None,
) -> None:
    """Накладывает реальный баланс/статус агента/прогноз/кассеты от atm_monitor поверх реестра.

    Сначала пробуем точное совпадение terminal_id == tid BTech. Реестр часто
    заведён под другую сеть (WAY4/UzCard и т.п.) с собственной нумерацией —
    тогда id не совпадёт, хотя это тот же физический банкомат. В этом случае
    подстраховываемся совпадением координат (см. atm_live.find_nearest_tid)."""
    own_tid = str(d.get("terminal_id"))
    live = live_by_tid.get(own_tid)
    match_method, live_tid = ("terminal_id", own_tid) if live else (None, None)

    if not live:
        nearest_tid = atm_live.find_nearest_tid(d.get("lat"), d.get("lon"))
        if nearest_tid:
            live = live_by_tid.get(nearest_tid)
            match_method, live_tid = "location", nearest_tid

    manual = d.get("balance") is not None
    d["balance_source"] = "manual" if manual else None
    if not live:
        d["live"] = False
        return

    d["live"] = True
    d["live_match"] = match_method
    d["live_tid"] = live_tid  # реальный tid BTech — нужен для /cassettes и /history, когда он отличается от terminal_id
    d["agent_status"] = live.get("agent_status")
    d["balance_polled_at"] = live.get("polled_at")
    d["last_incassation"] = live.get("last_incassation")

    manual_at, polled_at = _parse_utc(d.get("last_balance_at")), _parse_utc(live.get("polled_at"))
    if manual and manual_at is not None and (polled_at is None or manual_at > polled_at):
        # Ввод оператора свежее опроса: кассеты и прогноз опроса относятся
        # к старому остатку и не показываются.
        d["forecast_hours"] = None
        if cassettes_by_tid is not None:
            d["cassettes"] = []
        return

    if live.get("balance") is not None:
        d["balance"] = live["balance"]
        d["balance_source"] = "monitoring"
    d["forecast_hours"] = live.get("forecast_hours")
    if cassettes_by_tid is not None:
        # Список, не словарь по номиналу — у части моделей бывает несколько
        # кассет одного номинала (см. atm_live.cassettes_by_tid), их нельзя терять.
        d["cassettes"] = cassettes_by_tid.get(live_tid) or []


def _decorate_atm(d: Dict[str, Any]) -> Dict[str, Any]:
    cap = min(d.get("capacity") or DEFAULT_CAPACITY, MAX_CAPACITY)
    d["capacity"] = cap
    bal = d.get("balance")
    d["balance_pct"] = round((bal / cap) * 100, 1) if bal is not None and cap else None
    d["status"] = _compute_status(bal, cap)
    return d


def count_atms() -> int:
    with _connect() as conn:
        return int(conn.execute("SELECT COUNT(*) AS c FROM atms").fetchone()["c"])


def list_atms(
    region: Optional[str] = None,
    branch: Optional[str] = None,
    status: Optional[str] = None,
    limit: int = 1000,
    offset: int = 0,
) -> List[Dict[str, Any]]:
    sql = "SELECT * FROM atms WHERE 1=1"
    params: list[Any] = []
    if region:
        sql += " AND region = ?"
        params.append(region)
    if branch:
        sql += " AND branch = ?"
        params.append(branch)
    sql += " ORDER BY id LIMIT ? OFFSET ?"
    params.extend([limit, offset])

    live_by_tid = atm_live.latest_states_by_tid()
    cassettes_by_tid = atm_live.all_latest_cassettes()
    with _connect() as conn:
        raw_rows = [dict(r) for r in conn.execute(sql, params).fetchall()]

    rows = []
    for r in raw_rows:
        _apply_live_state(r, live_by_tid, cassettes_by_tid)
        rows.append(_decorate_atm(r))

    if status:
        rows = [r for r in rows if r["status"] == status]
    return rows


def get_atm(terminal_id: str) -> Optional[Dict[str, Any]]:
    with _connect() as conn:
        row = conn.execute("SELECT * FROM atms WHERE terminal_id = ?", (terminal_id,)).fetchone()
    if not row:
        return None
    d = dict(row)
    _apply_live_state(d, atm_live.latest_states_by_tid(), atm_live.all_latest_cassettes())
    return _decorate_atm(d)


def list_regions() -> List[Dict[str, Any]]:
    """Список областей с количеством ATM."""
    with _connect() as conn:
        rows = conn.execute("""
            SELECT region, COUNT(*) AS cnt
              FROM atms
             WHERE region IS NOT NULL AND region <> ''
             GROUP BY region
             ORDER BY cnt DESC
        """).fetchall()
    return [{"region": r["region"], "count": int(r["cnt"])} for r in rows]


def list_branches(region: Optional[str] = None) -> List[Dict[str, Any]]:
    """Список филиалов с количеством ATM."""
    sql = """
        SELECT branch, region, COUNT(*) AS cnt
          FROM atms
         WHERE branch IS NOT NULL AND branch <> ''
    """
    params: list[Any] = []
    if region:
        sql += " AND region = ?"
        params.append(region)
    sql += " GROUP BY branch, region ORDER BY cnt DESC"

    with _connect() as conn:
        rows = conn.execute(sql, params).fetchall()
    return [{"branch": r["branch"], "region": r["region"], "count": int(r["cnt"])} for r in rows]


# ═══════════════════════════════════════════════════════════
# ATM — ЗАПИСЬ
# ═══════════════════════════════════════════════════════════

def bulk_insert_atms(records: List[Dict[str, Any]]) -> Dict[str, int]:
    """Массовая вставка/обновление банкоматов по terminal_id."""
    stats = {"inserted": 0, "updated": 0, "skipped": 0}
    with _connect() as conn:
        for rec in records:
            terminal_id = (rec.get("terminal_id") or "").strip()
            if not terminal_id:
                stats["skipped"] += 1
                continue

            values = (
                rec.get("atm_number"), rec.get("branch_code"), rec.get("local_code"),
                rec.get("region"), rec.get("branch"), rec.get("model"),
                rec.get("network_type"), rec.get("serial"), rec.get("merchant_id"),
                rec.get("address"), rec.get("lat"), rec.get("lon"),
                min(rec.get("capacity") or DEFAULT_CAPACITY, MAX_CAPACITY),
            )

            exists = conn.execute(
                "SELECT 1 FROM atms WHERE terminal_id = ?", (terminal_id,)
            ).fetchone()

            if exists:
                conn.execute("""
                    UPDATE atms SET
                        atm_number = ?, branch_code = ?, local_code = ?, region = ?,
                        branch = ?, model = ?, network_type = ?, serial = ?,
                        merchant_id = ?, address = ?, lat = ?, lon = ?, capacity = ?,
                        updated_at = datetime('now')
                     WHERE terminal_id = ?
                """, (*values, terminal_id))
                stats["updated"] += 1
            else:
                conn.execute("""
                    INSERT INTO atms (
                        atm_number, branch_code, local_code, region, branch, model,
                        network_type, serial, merchant_id, address, lat, lon, capacity,
                        terminal_id
                    ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """, (*values, terminal_id))
                stats["inserted"] += 1
    return stats


def update_balance(terminal_id: str, balance: int) -> bool:
    """Обновляет остаток одного ATM."""
    with _connect() as conn:
        cur = conn.execute("""
            UPDATE atms
               SET balance = ?, last_balance_at = datetime('now'), updated_at = datetime('now')
             WHERE terminal_id = ?
        """, (int(balance), terminal_id))
        return cur.rowcount > 0


def bulk_update_balances(updates: List[Dict[str, Any]]) -> Dict[str, int]:
    """Массовое обновление остатков: [{terminal_id, balance}, ...]"""
    ok, fail = 0, 0
    with _connect() as conn:
        for u in updates:
            tid, bal = u.get("terminal_id"), u.get("balance")
            try:
                bal = None if bal is None else int(bal)
            except (TypeError, ValueError):
                bal = None
            if not tid or bal is None or not 0 <= bal <= MAX_CAPACITY:
                fail += 1
                continue
            cur = conn.execute("""
                UPDATE atms
                   SET balance = ?, last_balance_at = datetime('now'), updated_at = datetime('now')
                 WHERE terminal_id = ?
            """, (int(bal), tid))
            if cur.rowcount:
                ok += 1
            else:
                fail += 1
    return {"updated": ok, "failed": fail}


# ═══════════════════════════════════════════════════════════
# ФИЛИАЛЫ
# ═══════════════════════════════════════════════════════════

def count_branches() -> int:
    with _connect() as conn:
        return int(conn.execute("SELECT COUNT(*) AS c FROM branches").fetchone()["c"])


def list_branches_full(
    region: Optional[str] = None,
    incassation: Optional[int] = None,
    limit: int = 5000,
    offset: int = 0,
) -> List[Dict[str, Any]]:
    sql = "SELECT * FROM branches WHERE 1=1"
    params: list[Any] = []
    if region:
        sql += " AND region = ?"
        params.append(region)
    if incassation is not None:
        sql += " AND incassation = ?"
        params.append(int(incassation))
    sql += " ORDER BY id LIMIT ? OFFSET ?"
    params.extend([limit, offset])

    with _connect() as conn:
        return [dict(r) for r in conn.execute(sql, params).fetchall()]


def get_branch(local_code: str) -> Optional[Dict[str, Any]]:
    with _connect() as conn:
        row = conn.execute("SELECT * FROM branches WHERE local_code = ?", (local_code,)).fetchone()
    return dict(row) if row else None


def bulk_insert_branches(records: List[Dict[str, Any]]) -> Dict[str, int]:
    """Массовая вставка/обновление филиалов по local_code."""
    stats = {"inserted": 0, "updated": 0, "skipped": 0}
    with _connect() as conn:
        for rec in records:
            local_code = (rec.get("local_code") or "").strip() or None
            values = (
                rec.get("number"), rec.get("region"), rec.get("address"),
                rec.get("lat"), rec.get("lon"), int(rec.get("incassation") or 0),
            )

            exists = None
            if local_code:
                exists = conn.execute(
                    "SELECT 1 FROM branches WHERE local_code = ?", (local_code,)
                ).fetchone()

            if exists:
                conn.execute("""
                    UPDATE branches SET
                        number = ?, region = ?, address = ?, lat = ?, lon = ?,
                        incassation = ?, updated_at = datetime('now')
                     WHERE local_code = ?
                """, (*values, local_code))
                stats["updated"] += 1
            else:
                conn.execute("""
                    INSERT INTO branches (number, region, address, lat, lon, incassation, local_code)
                    VALUES (?,?,?,?,?,?,?)
                """, (*values, local_code))
                stats["inserted"] += 1
    return stats


def replace_incassation_trips(cars: List[Dict[str, Any]]) -> int:
    """Replace the saved calendar with the newly built trips."""
    with _connect() as conn:
        conn.execute("DELETE FROM incassation_trips")
        conn.execute("DELETE FROM sqlite_sequence WHERE name = 'incassation_trips'")
        for car in cars:
            dep = car.get("departure_branch") or {}
            conn.execute(
                """
                INSERT INTO incassation_trips (
                    planned_date, region, label, priority,
                    branch_local_code, branch_address, branch_lat, branch_lon,
                    stops_json, geometry_json, distance_km, est_time_min, refill_total
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    car.get("planned_date"),
                    car.get("region"),
                    car.get("label"),
                    car.get("priority") or "planned",
                    dep.get("local_code"),
                    dep.get("address"),
                    dep.get("lat"),
                    dep.get("lon"),
                    json.dumps(car.get("stops") or [], ensure_ascii=False),
                    json.dumps(car.get("geometry") or [], ensure_ascii=False),
                    car.get("distance_km") or 0,
                    car.get("est_time_min") or 0,
                    car.get("refill_total") or 0,
                ),
            )
        return len(cars)


def list_incassation_trips() -> List[Dict[str, Any]]:
    with _connect() as conn:
        rows = conn.execute(
            "SELECT * FROM incassation_trips ORDER BY planned_date, region, id"
        ).fetchall()
    out = []
    for r in rows:
        item = dict(r)
        try:
            item["stops"] = json.loads(item.pop("stops_json") or "[]")
        except json.JSONDecodeError:
            item["stops"] = []
        try:
            item["geometry"] = json.loads(item.pop("geometry_json") or "[]")
        except json.JSONDecodeError:
            item["geometry"] = []
        out.append(item)
    return out
