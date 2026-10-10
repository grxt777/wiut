import logging

import requests

from .auth import token_manager
from .config import settings

logger = logging.getLogger("atm_monitor.api_client")


def _headers() -> dict:
    return {
        "accept": "application/json, text/plain, */*",
        "authorization": f"Bearer {token_manager.get_token()}",
    }


def fetch_all_atms() -> list:
    """Тянет все банкоматы постранично (offset/limit), пока не кончатся записи.
    При 401 один раз принудительно обновляет токен и повторяет запрос."""

    url = settings.API_BASE_URL.rstrip("/") + settings.ATM_LIST_PATH
    items: list = []
    offset = 0

    while True:
        params = dict(settings.QUERY_PARAMS)
        params["offset"] = offset
        params["limit"] = settings.PAGE_LIMIT

        resp = requests.get(
            url, params=params, headers=_headers(), timeout=settings.REQUEST_TIMEOUT
        )

        if resp.status_code == 401:
            logger.warning("Получили 401, принудительно обновляем токен и повторяем запрос")
            token_manager.force_refresh()
            resp = requests.get(
                url, params=params, headers=_headers(), timeout=settings.REQUEST_TIMEOUT
            )

        resp.raise_for_status()
        data = resp.json()

        # API может отдавать либо голый массив, либо {"items": [...], "total": N}
        batch = data.get("items", []) if isinstance(data, dict) else data
        if not batch:
            break

        items.extend(batch)
        logger.info("Загружено %d записей (offset=%d)", len(batch), offset)

        if len(batch) < settings.PAGE_LIMIT:
            break
        offset += settings.PAGE_LIMIT

    return items
