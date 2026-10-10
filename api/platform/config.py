"""
Единая конфигурация Bank Intelligence Platform.

Объединяет настройки двух доменов:
  * Cashier Intelligence — аналитика кассиров
  * ATM Monitor         — реестр банкоматов, остатки, инкассация

Хранилище — SQLite: обычный файл, никаких серверов и настройки окружения.
"""

import os
from pathlib import Path

from api.analytics.config import ATM_MAX_CASH_UZS

# ── Пути ────────────────────────────────────────────────────
BASE_DIR = Path(__file__).resolve().parents[2]   # корень проекта
DATA_DIR = BASE_DIR / "data"
DATA_DIR.mkdir(exist_ok=True)

# ── База данных (SQLite) ─────────────────────────────────────
# Путь можно переопределить переменной окружения DB_PATH.
DB_PATH = Path(os.getenv("DB_PATH", str(DATA_DIR / "bank.db")))

# ── Бизнес-пороги ATM ────────────────────────────────────────
LOW_CASH_PCT      = float(os.getenv("LOW_CASH_PCT", "0.20"))      # критичный уровень (cash-out)
WARNING_CASH_PCT  = float(os.getenv("WARNING_CASH_PCT", "0.40"))  # уровень предупреждения
MAX_CAPACITY      = ATM_MAX_CASH_UZS                                   # физический предел банкомата, UZS
DEFAULT_CAPACITY  = min(int(os.getenv("DEFAULT_CAPACITY", str(MAX_CAPACITY))), MAX_CAPACITY)

# ── Геометрия / маршруты инкассации ─────────────────────────
DEPOT = {"lat": 41.3111, "lon": 69.2797, "name": "Центральный депо (Ташкент)"}
ROAD_FACTOR = 1.35      # коэффициент дорожной сети к прямому расстоянию
AVG_SPEED_KMH = 30      # средняя скорость инкассаторской машины
