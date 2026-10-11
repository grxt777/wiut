import os
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parents[1]
load_dotenv(BASE_DIR / ".env")


class Settings:
    # Отдельный SQLite-файл телеметрии (в bank.db уже есть своя таблица atms — не смешиваем).
    DB_PATH = os.getenv("ATM_MONITOR_DB_PATH", str(BASE_DIR / "data" / "atm_monitor.db"))


settings = Settings()
