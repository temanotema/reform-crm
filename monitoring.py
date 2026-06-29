"""
monitoring.py — подключение Sentry (отслеживание ошибок).

Пока SENTRY_DSN пустой — функция ничего не делает (мониторинг выключен).
Когда впишешь ключ в config_local.py, события начнут уходить в Sentry.

Важно (152-ФЗ): персональные данные в события НЕ отправляются —
тело запросов, cookies и пользователь отбрасываются, а телефоны
вырезаются из текстов ошибок. Наружу уходит только техническая
информация (где и почему сломалось).
"""

import re
import logging

logger = logging.getLogger(__name__)

# Последовательности цифр (телефоны и т.п.) → заменяем заглушкой.
_PHONE_RE = re.compile(r"\d[\d\-\s()]{6,}\d")

_inited = False


def _scrub(value):
    if isinstance(value, str):
        return _PHONE_RE.sub("[номер]", value)
    return value


def _before_send(event, hint):
    # Безвредный «левый» SIGINT при старте не шлём в Sentry (в консоли он остаётся).
    _msg = event.get("message") or (event.get("logentry") or {}).get("message") or ""
    if "SIGINT" in _msg:
        return None

    # Не отправляем тело запроса, cookies и данные пользователя.
    req = event.get("request")
    if isinstance(req, dict):
        req.pop("data", None)
        req.pop("cookies", None)
        headers = req.get("headers")
        if isinstance(headers, dict):
            headers.pop("Cookie", None)
            headers.pop("cookie", None)
    event.pop("user", None)

    # Вырезаем телефоны из текста сообщения и исключений.
    if event.get("message"):
        event["message"] = _scrub(event["message"])
    for ex in (event.get("exception", {}) or {}).get("values", []) or []:
        if ex.get("value"):
            ex["value"] = _scrub(ex["value"])
    return event


def init_sentry(component: str) -> bool:
    """Включает Sentry, если задан DSN. Возвращает True, если включён."""
    global _inited
    if _inited:
        return True
    try:
        from config import SENTRY_DSN, SENTRY_ENVIRONMENT
    except Exception:
        return False

    dsn = (SENTRY_DSN or "").strip()
    if not dsn:
        return False  # выключено, пока ключ не задан

    try:
        import sentry_sdk
        from sentry_sdk.integrations.logging import LoggingIntegration
    except Exception:
        logger.warning("sentry-sdk не установлен — мониторинг выключен (pip install sentry-sdk)")
        return False

    logging_integration = LoggingIntegration(
        level=logging.INFO,         # INFO и выше — как «хлебные крошки» (контекст)
        event_level=logging.WARNING,  # WARNING и выше — отдельные события в Sentry
    )

    sentry_sdk.init(
        dsn=dsn,
        environment=SENTRY_ENVIRONMENT,
        send_default_pii=False,        # не цеплять IP/куки/тело автоматически
        traces_sample_rate=0.0,        # трейсинг производительности не нужен
        integrations=[logging_integration],
        before_send=_before_send,
    )
    try:
        sentry_sdk.set_tag("component", component)
    except Exception:
        pass

    _inited = True
    logger.info("Sentry включён (%s, env=%s)", component, SENTRY_ENVIRONMENT)
    return True
