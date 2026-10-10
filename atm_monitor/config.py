import os
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parents[1]  # корень проекта integratsya
load_dotenv(BASE_DIR / ".env")


def _bool(name: str, default: str) -> bool:
    return os.getenv(name, default).strip().lower() in ("1", "true", "yes")


class Settings:
    # --- Platform base ---
    API_BASE_URL = os.getenv("API_BASE_URL", "https://monitoring.btech.uz")
    ATM_LIST_PATH = os.getenv("ATM_LIST_PATH", "/api/base/atm/")

    # --- Login / auth ---
    # Подтверждено по вашему логин-запросу в браузере.
    LOGIN_PATH = os.getenv("LOGIN_PATH", "/api/login")
    AUTH_USERNAME = os.getenv("AUTH_USERNAME", "")
    AUTH_PASSWORD = os.getenv("AUTH_PASSWORD", "")

    # Названия полей в теле запроса логина и в ответе — подтверждены по вашему запросу.
    LOGIN_USERNAME_FIELD = os.getenv("LOGIN_USERNAME_FIELD", "email")
    LOGIN_PASSWORD_FIELD = os.getenv("LOGIN_PASSWORD_FIELD", "password")
    LOGIN_TOKEN_RESPONSE_FIELD = os.getenv("LOGIN_TOKEN_RESPONSE_FIELD", "token")

    # --- Database ---
    # Отдельный SQLite-файл (в bank.db уже есть своя таблица atms — не смешиваем).
    DB_PATH = os.getenv("ATM_MONITOR_DB_PATH", str(BASE_DIR / "data" / "atm_monitor.db"))

    # --- Scheduling ---
    POLL_INTERVAL_HOURS = int(os.getenv("POLL_INTERVAL_HOURS", "1"))
    REQUEST_TIMEOUT = int(os.getenv("REQUEST_TIMEOUT", "30"))
    PAGE_LIMIT = int(os.getenv("PAGE_LIMIT", "500"))
    RUN_IMMEDIATELY_ON_START = _bool("RUN_IMMEDIATELY_ON_START", "true")
    # Вместе с сервером запускать фоновый сборщик (ATM_COLLECTOR=0 — выключить)
    COLLECTOR_ENABLED = _bool("ATM_COLLECTOR", "true")
    TIMEZONE = os.getenv("TIMEZONE", "Asia/Tashkent")

    # --- Query params (как в примере пользователя; agentConnectionStatus="all",
    # чтобы получать в т.ч. офлайн-банкоматы, а не только online) ---
    QUERY_PARAMS = {
        "clientId": os.getenv("FILTER_CLIENT_ID", ""),
        "vendorId": os.getenv("FILTER_VENDOR_ID", ""),
        "modelId": os.getenv("FILTER_MODEL_ID", ""),
        "functionId": os.getenv("FILTER_FUNCTION_ID", ""),
        "variantId": os.getenv("FILTER_VARIANT_ID", ""),
        "atmGroupId": os.getenv("FILTER_ATM_GROUP_ID", ""),
        "countryId": os.getenv("FILTER_COUNTRY_ID", ""),
        "regionId": os.getenv("FILTER_REGION_ID", ""),
        "cityId": os.getenv("FILTER_CITY_ID", ""),
        "hashTags": os.getenv("FILTER_HASH_TAGS", ""),
        "appConnectionStatus": os.getenv("FILTER_APP_CONN_STATUS", "all"),
        "agentConnectionStatus": os.getenv("FILTER_AGENT_CONN_STATUS", "all"),
        "hwFaults": os.getenv("FILTER_HW_FAULTS", ""),
        "atmStatus": os.getenv("FILTER_ATM_STATUS", "all"),
        "withUnitsTurnoverTotal": "true",
        "lang": os.getenv("FILTER_LANG", "ru"),
    }


settings = Settings()
