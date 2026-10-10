import base64
import json
import logging
import re
import time

import requests

from .config import settings

logger = logging.getLogger("atm_monitor.auth")

# Похоже на JWT: три base64url-блока через точку.
_JWT_RE = re.compile(r"^[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}$")


def _find_jwt_recursive(node, _depth: int = 0):
    """Резервный способ найти токен: если настроенный LOGIN_TOKEN_RESPONSE_FIELD
    не совпал с реальным ответом API, ищем в ответе любую строку, похожую на JWT,
    на случай если сервер называет поле как-то иначе (accessToken, jwt, data.token...)."""
    if _depth > 4:
        return None
    if isinstance(node, str) and _JWT_RE.match(node):
        return node
    if isinstance(node, dict):
        for v in node.values():
            found = _find_jwt_recursive(v, _depth + 1)
            if found:
                return found
    if isinstance(node, list):
        for v in node:
            found = _find_jwt_recursive(v, _depth + 1)
            if found:
                return found
    return None


# Для заголовков и кук допускаем префикс вроде "Bearer " перед токеном.
_JWT_SEARCH_RE = re.compile(r"[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}")


def _find_jwt_in_headers(headers) -> "str | None":
    """Ищем JWT прямо в заголовках ответа (например X-Token, Authorization)
    и в Set-Cookie — некоторые API отдают токен не в теле, а в заголовке/куке."""
    for name, value in headers.items():
        match = _JWT_SEARCH_RE.search(value)
        if match:
            logger.info("Нашли токен в заголовке ответа '%s'.", name)
            return match.group(0)
    return None


class TokenManager:
    """Логинится на платформу и держит актуальный Bearer-токен.

    Токен обновляется автоматически:
    - при первом обращении;
    - когда до истечения (поле 'exp' в JWT) остаётся < 60 секунд;
    - принудительно, если API вернул 401 (см. force_refresh, используется в api_client).
    """

    def __init__(self):
        self._token = None
        self._exp = 0

    def get_token(self) -> str:
        if not self._token or time.time() > self._exp - 60:
            self._login()
        return self._token

    def force_refresh(self) -> str:
        self._login()
        return self._token

    def _login(self):
        if not settings.AUTH_USERNAME or not settings.AUTH_PASSWORD:
            raise RuntimeError(
                "AUTH_USERNAME / AUTH_PASSWORD не заданы (см. .env). "
                "Без учётных данных сервис не может залогиниться сам."
            )

        url = settings.API_BASE_URL.rstrip("/") + settings.LOGIN_PATH
        payload = {
            settings.LOGIN_USERNAME_FIELD: settings.AUTH_USERNAME,
            settings.LOGIN_PASSWORD_FIELD: settings.AUTH_PASSWORD,
        }

        logger.info("Логинимся на платформу (%s)...", url)
        resp = requests.post(
            url,
            json=payload,
            params={"lang": settings.QUERY_PARAMS.get("lang", "ru")},
            timeout=settings.REQUEST_TIMEOUT,
        )
        resp.raise_for_status()
        data = resp.json()

        token = self._extract_token(data)
        if not token:
            # Настроенный ключ не сработал — пробуем найти JWT-строку
            # где угодно в теле ответа, вдруг сервер называет поле иначе.
            token = _find_jwt_recursive(data)
            if token:
                logger.warning(
                    "Токен не нашёлся по ключу '%s', но найден автоматически "
                    "(похожая на JWT строка) в другом месте тела ответа.",
                    settings.LOGIN_TOKEN_RESPONSE_FIELD,
                )

        if not token:
            # В теле нет ничего похожего на JWT — пробуем заголовки ответа
            # (включая Set-Cookie): некоторые API отдают токен именно так.
            token = _find_jwt_in_headers(resp.headers)

        if not token:
            raise RuntimeError(
                f"Не нашли токен ни в теле ответа (ключ '{settings.LOGIN_TOKEN_RESPONSE_FIELD}' "
                f"и автопоиск JWT), ни в заголовках ответа. Ответ сервера: {data}. "
                f"Заголовки ответа: {dict(resp.headers)}. "
                f"Похоже, токен приходит через cookie с httpOnly (недоступно для чтения "
                f"скриптом) — нужно смотреть вкладку Application → Cookies в DevTools."
            )

        self._token = token
        self._exp = self._decode_exp(token)
        logger.info("Токен обновлён, истекает: %s", time.ctime(self._exp))

    def _extract_token(self, data):
        # Поддержка как плоского ответа {"token": "..."}, так и вложенного
        # {"data": {"token": "..."}} — распространённый вариант API.
        if isinstance(data, dict):
            if settings.LOGIN_TOKEN_RESPONSE_FIELD in data:
                return data[settings.LOGIN_TOKEN_RESPONSE_FIELD]
            nested = data.get("data")
            if isinstance(nested, dict) and settings.LOGIN_TOKEN_RESPONSE_FIELD in nested:
                return nested[settings.LOGIN_TOKEN_RESPONSE_FIELD]
        return None

    @staticmethod
    def _decode_exp(token: str) -> float:
        """Достаём 'exp' прямо из тела JWT, не проверяя подпись — нам нужно
        только знать, когда токен протухнет, чтобы обновить его заранее."""
        try:
            payload_b64 = token.split(".")[1]
            padded = payload_b64 + "=" * (-len(payload_b64) % 4)
            payload = json.loads(base64.urlsafe_b64decode(padded))
            return float(payload.get("exp", time.time() + 3600))
        except Exception:
            logger.warning("Не удалось распарсить exp из токена, ставим запас 1 час.")
            return time.time() + 3600


token_manager = TokenManager()
