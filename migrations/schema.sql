-- Bank Intelligence Platform — справочная схема (SQLite).
-- Таблицы создаются автоматически при первом запуске (api/core/db.py
-- и api/core/cashier/repository.py). Этот файл нужен только для обзора.

-- ── Домен ATM ───────────────────────────────────────────────
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
);

CREATE INDEX IF NOT EXISTS idx_atms_region ON atms(region);
CREATE INDEX IF NOT EXISTS idx_atms_branch ON atms(branch);
CREATE INDEX IF NOT EXISTS idx_atms_local  ON atms(local_code);

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
);

CREATE INDEX IF NOT EXISTS idx_branches_region ON branches(region);
CREATE INDEX IF NOT EXISTS idx_branches_inc    ON branches(incassation);

-- ── Домен кассиров ──────────────────────────────────────────
CREATE TABLE IF NOT EXISTS cashier_imports (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    filename     TEXT,
    imported_at  TEXT NOT NULL,
    rows_count   INTEGER NOT NULL,
    report_label TEXT
);

CREATE TABLE IF NOT EXISTS cashier_reports (
    id                    INTEGER PRIMARY KEY AUTOINCREMENT,
    import_id             INTEGER NOT NULL REFERENCES cashier_imports(id) ON DELETE CASCADE,
    tab_number            TEXT,
    employee_number       TEXT,
    full_name             TEXT NOT NULL,
    position              TEXT,
    days_worked           REAL,
    std_days              REAL NOT NULL DEFAULT 0,
    operations_count      REAL NOT NULL DEFAULT 0,
    operations_minutes    REAL NOT NULL DEFAULT 0,
    bek_count             REAL NOT NULL DEFAULT 0,
    bek_minutes           REAL NOT NULL DEFAULT 0,
    front_count           REAL NOT NULL DEFAULT 0,
    front_minutes         REAL NOT NULL DEFAULT 0,
    load_percent          REAL NOT NULL DEFAULT 0,
    load_difference       REAL NOT NULL DEFAULT 0,
    branch_name           TEXT,
    raw_note              TEXT,
    hr_status_code        TEXT,
    hr_status_label       TEXT,
    replacing_full_name   TEXT,
    replaced_by_full_name TEXT,
    has_replacement       INTEGER DEFAULT 1,
    metrics_json          TEXT NOT NULL DEFAULT '{}',
    discipline_type       TEXT,
    discipline_date_order TEXT,
    discipline_reason     TEXT
);

CREATE TABLE IF NOT EXISTS cashier_status_imports (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    filename    TEXT,
    imported_at TEXT NOT NULL,
    rows_count  INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS cashier_statuses (
    id                    INTEGER PRIMARY KEY AUTOINCREMENT,
    import_id             INTEGER NOT NULL REFERENCES cashier_status_imports(id) ON DELETE CASCADE,
    branch_name           TEXT,
    position              TEXT,
    full_name             TEXT NOT NULL,
    status_code           TEXT NOT NULL,
    status_label          TEXT NOT NULL,
    raw_note              TEXT,
    replacing_full_name   TEXT,
    replaced_by_full_name TEXT,
    has_replacement       INTEGER NOT NULL DEFAULT 0
);

CREATE INDEX IF NOT EXISTS idx_cashier_reports_import   ON cashier_reports(import_id);
CREATE INDEX IF NOT EXISTS idx_cashier_reports_status   ON cashier_reports(hr_status_code);
CREATE INDEX IF NOT EXISTS idx_cashier_reports_position ON cashier_reports(position);
CREATE INDEX IF NOT EXISTS idx_cashier_statuses_import  ON cashier_statuses(import_id);
CREATE INDEX IF NOT EXISTS idx_cashier_statuses_status  ON cashier_statuses(status_code);
